"""One conversion case; executed in a disposable child process by the CLI."""
from __future__ import annotations

import hashlib
import json
import math
import platform
from importlib.metadata import version, PackageNotFoundError
import runpy
import time
import traceback
from dataclasses import asdict
from pathlib import Path

from OCP.BRepCheck import BRepCheck_Analyzer

from step2code.codegen import StepEmitter, CODE_STRUCTURE_SPEC
from step2code.step_import import read_reference
from json2scapi.evaluation.metrics import read_step, solid_count, volume
from json2scapi.runtime.worker import compound, export_step, returned_shapes
from step2code import __version__
from step2code.recognition import RecognitionOptions, UnsupportedGeometry, recognize
from step2code.validation import evaluate, verify_source_coverage


def write_json(path, value):
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    temporary.replace(path)


def convert_case(source: Path, output: Path, *, samples=8192, mode='world',
                 options: RecognitionOptions | None = None):
    source, output = source.resolve(), output.resolve()
    if output.exists() and any(p.name != 'worker.log' for p in output.iterdir()):
        raise FileExistsError(f'Case output must be empty: {output}')
    output.mkdir(parents=True, exist_ok=True)
    options = options or RecognitionOptions()
    report_path = output/'report.json'
    if report_path.exists():
        raise FileExistsError(f'Case already has a report: {output}')
    started = time.monotonic()
    dependencies = {'python': platform.python_version()}
    for package in ('simplecadapi', 'cadquery-ocp', 'numpy', 'scipy'):
        try:
            dependencies[package] = version(package)
        except PackageNotFoundError:
            dependencies[package] = 'unknown'
    report = {'runtime_versions': dependencies, 'tool': 'step2scapi', 'version': __version__, 'input': str(source),
              'output': str(output), 'status': 'running', 'stage': 'read_reference',
              'configuration': {'samples': samples, 'mode': mode, 'recognition': asdict(options)},
              'history_recovered': False, 'artifacts': {},
              'code_structure_spec': CODE_STRUCTURE_SPEC, 'training_task': 'geometry_reconstruction',
              'geometry_validated': None,
              'parameter_edit_validation': {'status':'not_run', 'scope':'default parameters only'},
              'assembly_validation': {'status':'unsupported', 'scope':'flattened solids; source hierarchy not recovered'}}

    def checkpoint(stage):
        report['stage'] = stage
        report['elapsed_seconds'] = time.monotonic()-started
        write_json(report_path, report)

    try:
        checkpoint('read_reference')
        with source.open('rb') as stream:
            report['input_sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
        reference, report['import'] = read_reference(source)
        checkpoint('recognize')
        payload, evidence = recognize(reference, source, options)
        report['recognition'] = evidence
        payload['extensions']['step2scapi']['import'] = report['import']
        write_json(output/'model.ir.json', payload)
        report['artifacts']['ir'] = 'model.ir.json'
        checkpoint('generate')
        if payload.get('format') == 'step-reconstruction-plan-v1':
            from step2code.composite_codegen import CompositeEmitter
            emitter = CompositeEmitter(payload, evidence)
        else:
            emitter = StepEmitter(payload, evidence)
        code = emitter.emit()
        code_path = output/'model.scapi.py'
        code_path.write_text(code, encoding='utf-8')
        symbols = emitter.metadata
        symbols['step2scapi'] = {'recognizer': evidence['recognizer'], 'source': str(source),
                                'history_recovered': False, 'input_sha256': report['input_sha256'],
                                'import': report['import'], 'runtime_versions': dependencies}
        write_json(output/'model.symbols.json', symbols)
        write_json(output/'model.prepared.ir.json', emitter.payload)
        report['structure'] = symbols['structure']
        report['artifacts'].update(code=code_path.name, symbols='model.symbols.json',
                                   prepared_ir='model.prepared.ir.json')
        checkpoint('build')
        namespace = runpy.run_path(str(code_path))
        shapes = returned_shapes(namespace['build']())
        if not shapes or any(s.IsNull() or solid_count(s)<1 or not BRepCheck_Analyzer(s).IsValid() for s in shapes):
            raise ValueError('Generated build() returned empty or invalid solids')
        candidate = compound(shapes)
        if not math.isfinite(volume(candidate)) or volume(candidate)<=0:
            raise ValueError('Generated build() returned nonpositive volume')
        report['build'] = {'valid': True, 'body_count': len(shapes), 'solid_count': solid_count(candidate)}
        checkpoint('source_coverage')
        report['source_coverage_validation'] = verify_source_coverage(
            reference, shapes, evidence['source_coverage'],
            tolerance=options.reconstruction_relative_tolerance)
        checkpoint('export')
        step_path = output/'model.step'
        export_step(shapes, step_path)
        restored = read_step(step_path)
        report['artifacts']['step'] = step_path.name
        checkpoint('roundtrip')
        report['roundtrip'] = evaluate(candidate, restored, samples=samples, mode='world')
        checkpoint('validate')
        report['geometry'] = evaluate(reference, restored, samples=samples, mode=mode)
        decisions = [report['source_coverage_validation']['decision'], report['roundtrip']['decision'], report['geometry']['decision']]
        # A confirmed mismatch takes precedence; uncertainty never means pass.
        selected = next((d for d in decisions if d['status']=='rejected'),
                        next((d for d in decisions if d['status']=='provisional'), decisions[-1]))
        report.update(status=selected['status'], decision=selected)
    except UnsupportedGeometry as exc:
        report.update(status='provisional', decision={'status': 'provisional', 'reason': 'unsupported_geometry'},
                      error=str(exc), recognition_failure=exc.diagnostics)
    except Exception as exc:
        stage = report['stage']
        status = 'provisional' if stage in ('read_reference', 'roundtrip', 'validate') else 'rejected'
        report.update(status=status, decision={'status': status, 'reason': stage+'_error'},
                      error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc(limit=8))
    report['geometry_validated'] = {'train_ready':True, 'rejected':False}.get(report['status'])
    checkpoint('finished')
    return report
