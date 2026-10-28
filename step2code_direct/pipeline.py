"""One complete direct conversion, including actual execution and shared metrics."""
from dataclasses import asdict
import hashlib
from importlib.metadata import version, PackageNotFoundError
import json
import math
from pathlib import Path
import platform
import runpy
import time
import traceback

from OCP.BRepCheck import BRepCheck_Analyzer
from json2scapi.evaluation.metrics import volume, solid_count, read_step, symmetric_difference_volume
from json2scapi.runtime.worker import returned_shapes, compound, export_step
from step2code_direct.compat.import_step import read_reference
from step2code_direct.compat.validation import evaluate
from step2code_direct import __version__, CAPABILITIES
from step2code_direct.geometry import Options, UnsupportedGeometry, source_solids, recognize_prism
from step2code_direct.surface_analysis import analyze_solid, failure_category
from step2code_direct.emitter import DirectEmitter, CODE_STRUCTURE_SPEC

ROOT = Path(__file__).resolve().parent.parent


def write_json(path, payload):
    path = Path(path)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    for attempt in range(6):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 5:
                raise
            # Windows readers/AV can briefly hold the destination without
            # delete sharing. Preserve atomic replacement and retry briefly.
            time.sleep(0.01 * (2 ** attempt))


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def implementation_fingerprints():
    """Freeze source identities for comparisons even when the baseline evolves."""
    files = ['step2code_direct/'+name for name in ('__init__.py', 'geometry.py', 'surface_analysis.py', 'revolution.py', 'partition.py', 'emitter.py', 'hybrid.py', 'hybrid_search.py', 'csg_first.py', 'occupancy_search.py', 'search_guidance.py', 'hybrid_codegen.py', 'pipeline.py', 'cli.py')]
    files += ['step2code_direct/compat/import_step.py', 'step2code_direct/compat/validation.py', 'json2scapi/runtime/worker.py',
              'json2scapi/evaluation/metrics.py', 'json2scapi/evaluation/policy.py',
              'json2scapi/evaluation/registration.py', 'json2scapi/evaluation/similarity.py']
    return {name: file_hash(ROOT/name) for name in files}


def verify_bodies(sources, outputs, tolerance):
    if len(sources) != len(outputs) or any(solid_count(s) != 1 for s in outputs):
        return dict(decision={'status':'rejected', 'reason':'source_body_count_mismatch'})
    instances = []
    try:
        for index, (source, output) in enumerate(zip(sources, outputs), 1):
            error = symmetric_difference_volume(source, output)/volume(source)
            if not math.isfinite(error):
                raise ValueError('Nonfinite per-body symmetric difference')
            instances.append(dict(source_solid_index=index, output_body_index=index-1,
                                  symmetric_difference_relative=error, passed=error <= tolerance))
    except Exception as exc:
        return dict(instances=instances, error=f'{type(exc).__name__}: {exc}',
                    decision={'status':'provisional', 'reason':'source_body_measurement_error'})
    passed = all(item['passed'] for item in instances)
    return dict(instances=instances, decision={'status':'train_ready' if passed else 'rejected',
               'reason':'source_body_coverage_complete' if passed else 'source_body_geometry_mismatch'})


def convert_case(source, output, *, samples=8192, mode='world', options=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    options = options or Options()
    if samples < 256 or mode not in ('world', 'similarity'):
        raise ValueError('Invalid measurement configuration')
    if output.exists() and any(p.name != 'worker.log' for p in output.iterdir()):
        raise FileExistsError(f'Case output must be empty: {output}')
    output.mkdir(parents=True, exist_ok=True)
    runtime = {'python':platform.python_version()}
    for package in ('simplecadapi', 'cadquery-ocp', 'numpy', 'scipy'):
        try:
            runtime[package] = version(package)
        except PackageNotFoundError:
            runtime[package] = 'unknown'
    started = time.monotonic()
    last_time, last_stage = started, None
    report = dict(tool='step2scapi-direct', version=__version__, route='native_step_geometry_to_scapi',
        cad_ir_used=False, capabilities=CAPABILITIES, code_structure_spec=CODE_STRUCTURE_SPEC,
        input=str(source), output=str(output), status='running', stage='starting',
        configuration={'samples':samples, 'mode':mode, 'recognition':asdict(options)},
        runtime_versions=runtime, implementation_sha256=implementation_fingerprints(),
        history_recovered=False, training_task='geometry_reconstruction', artifacts={}, stage_times_seconds={},
        parameter_edit_validation={'status':'not_run', 'scope':'default parameters only'},
        assembly_validation={'status':'unsupported', 'scope':'flattened source solids; hierarchy not recovered'})

    def checkpoint(stage):
        nonlocal last_time, last_stage
        now = time.monotonic()
        if last_stage:
            times = report['stage_times_seconds']
            times[last_stage] = times.get(last_stage, 0)+(now-last_time)
        last_time, last_stage = now, stage
        report.update(stage=stage, elapsed_seconds=now-started)
        write_json(output/'report.json', report)

    try:
        checkpoint('read_reference')
        report['input_sha256'] = file_hash(source)
        reference, report['import'] = read_reference(source)
        sources = source_solids(reference, options)
        checkpoint('recognize_and_emit')
        emitter = DirectEmitter()
        surface_analyses = [analyze_solid(solid, options.length_tolerance) for solid in sources]
        report['recognition'] = {'recognizer':'direct-prism-hybrid-partition-'+options.hybrid_search+'-v6' if options.enable_hybrid else 'direct-analytic-prism-v2', 'features':[],
                                 'source_solid_count':len(sources), 'source_coverage':'pending',
                                 'surface_analysis':surface_analyses}
        for index, solid in enumerate(sources, 1):
            analysis = surface_analyses[index-1]
            if options.freeform_gate and analysis['recoverability'] == 'freeform_dominant':
                raise UnsupportedGeometry(
                    f'Solid {index}: freeform-dominant surface set is outside the exact analytic route',
                    {'source_solid_index':index, 'surface_analysis':analysis,
                     'failure_category':'freeform_surface', 'stage':'surface_gate'})
            try:
                native = recognize_prism(solid, index, options)
            except UnsupportedGeometry as prism_failure:
                if not options.enable_hybrid:
                    raise
                from step2code_direct.hybrid import recognize_hybrid
                from step2code_direct.hybrid_codegen import add_hybrid
                def progress(value):
                    report['hybrid_search'] = value
                    checkpoint('recognize_and_emit')
                try:
                    hybrid = recognize_hybrid(solid,index,options,on_progress=progress,
                                              seed_prisms=prism_failure.native_candidates)
                except UnsupportedGeometry as hybrid_failure:
                    if not options.enable_partition:
                        details = {'source_solid_index':index,'surface_analysis':analysis,
                                   'failure_category':failure_category(analysis, hybrid_failure.diagnostics),
                                   'prism':prism_failure.diagnostics,
                                   'hybrid':hybrid_failure.diagnostics}
                        raise UnsupportedGeometry(str(hybrid_failure), details) from hybrid_failure
                    try:
                        from step2code_direct.partition import recognize_partition
                        from step2code_direct.hybrid_codegen import add_partition
                        partition = recognize_partition(
                            solid, index, options,
                            initial_failure={'prism':prism_failure.diagnostics,
                                             'hybrid':hybrid_failure.diagnostics})
                    except UnsupportedGeometry as partition_failure:
                        details = {'source_solid_index':index,'surface_analysis':analysis,
                                   'failure_category':failure_category(analysis, {'hybrid':hybrid_failure.diagnostics,
                                                                                  'partition':partition_failure.diagnostics}),
                                   'prism':prism_failure.diagnostics,
                                   'hybrid':hybrid_failure.diagnostics,
                                   'partition':partition_failure.diagnostics}
                        raise UnsupportedGeometry(str(partition_failure), details) from partition_failure
                    add_partition(emitter, partition, index)
                    report['recognition']['features'].append(partition.evidence)
                    continue
                add_hybrid(emitter,hybrid,index)
                evidence = dict(hybrid.evidence,prior_prism_attempt=prism_failure.diagnostics)
                report['recognition']['features'].append(evidence)
            else:
                emitter.add_prism(native, index)
                report['recognition']['features'].append(native.evidence)
        report['recognition']['source_coverage'] = 'complete'
        code = emitter.emit()
        code_path = output/'model.scapi.py'
        code_path.write_text(code, encoding='utf-8')
        symbols = emitter.symbols
        symbols.update(input_sha256=report['input_sha256'], source=str(source))
        write_json(output/'model.symbols.json', symbols)
        report['artifacts'].update(code=code_path.name, symbols='model.symbols.json')
        report['structure'] = symbols['structure']
        checkpoint('build')
        namespace = runpy.run_path(str(code_path))
        shapes = returned_shapes(namespace['build']())
        if not shapes or any(s.IsNull() or solid_count(s) < 1 or not BRepCheck_Analyzer(s).IsValid() for s in shapes):
            raise ValueError('Generated build() returned empty or invalid geometry')
        candidate = compound(shapes)
        candidate_volume = volume(candidate)
        if not math.isfinite(candidate_volume) or candidate_volume <= 0:
            raise ValueError('Generated build() returned nonpositive volume')
        report['build'] = dict(valid=True, body_count=len(shapes), solid_count=solid_count(candidate))
        checkpoint('source_coverage')
        report['source_coverage_validation'] = verify_bodies(sources, shapes, options.reconstruction_relative_tolerance)
        checkpoint('export')
        export_step(shapes, output/'model.step')
        restored = read_step(output/'model.step')
        report['artifacts']['step'] = 'model.step'
        checkpoint('roundtrip')
        report['roundtrip'] = evaluate(candidate, restored, samples=samples, mode='world')
        checkpoint('validate')
        report['geometry'] = evaluate(reference, restored, samples=samples, mode=mode)
        decisions = [report[key]['decision'] for key in ('source_coverage_validation', 'roundtrip', 'geometry')]
        selected = next((d for d in decisions if d['status'] == 'rejected'),
                        next((d for d in decisions if d['status'] == 'provisional'), decisions[-1]))
        report.update(status=selected['status'], decision=selected)
    except UnsupportedGeometry as exc:
        report.update(status='provisional', decision={'status':'provisional', 'reason':'unsupported_geometry'},
                      failure_stage=report['stage'], error=str(exc), recognition_failure=exc.diagnostics)
    except Exception as exc:
        stage = report['stage']
        status = 'provisional' if stage in ('read_reference', 'roundtrip', 'validate') else 'rejected'
        report.update(status=status, decision={'status':status, 'reason':stage+'_error'}, failure_stage=stage,
                      error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc(limit=8))
    report['geometry_validated'] = {'train_ready':True, 'rejected':False}.get(report['status'])
    checkpoint('finished')
    return report
