"""Bounded independent workers and comparable per-file JSON/CSV summaries."""
import argparse
from collections import Counter
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import subprocess
import sys
import time

from step2code_direct import __version__, CAPABILITIES

ROOT = Path(__file__).resolve().parent.parent


def parser():
    result = argparse.ArgumentParser(description='STEP -> direct readable SCAPI (no CAD IR) -> CD/IoU/volume validation')
    result.add_argument('--input', required=True, type=Path)
    result.add_argument('--out', type=Path, help='New or empty output directory')
    result.add_argument('--recursive', action='store_true')
    result.add_argument('--limit', type=int)
    result.add_argument('--samples', type=int, default=8192)
    result.add_argument('--validation-mode', choices=('world', 'similarity'), default='world')
    result.add_argument('--timeout', type=float, default=300)
    result.add_argument('--length-tolerance', type=float, default=1e-6)
    result.add_argument('--reconstruction-relative-tolerance', type=float, default=1e-7)
    result.add_argument('--max-candidates', type=int, default=64)
    result.add_argument('--max-solids', type=int, default=64)
    result.add_argument('--no-hybrid', action='store_true', help='Use only the original direct-prism route')
    result.add_argument('--max-hybrid-atoms', type=int, default=48)
    result.add_argument('--max-boolean-steps', type=int, default=8)
    result.add_argument('--max-boolean-trials', type=int, default=256)
    result.add_argument('--hybrid-beam-width', type=int, default=3)
    result.add_argument('--hybrid-timeout', type=float, default=20.0, help='Seconds per source solid for bounded hybrid search')
    result.add_argument('--hybrid-search', choices=('lookahead', 'guided', 'legacy', 'csg-first'), default='lookahead',
                        help='Primitive-first CSG with residual sketches, occupancy lookahead, guided or legacy')
    result.add_argument('--no-partition', action='store_true', help='Disable the planar partition fallback')
    result.add_argument('--max-partition-axes', type=int, default=2)
    result.add_argument('--max-partition-parts', type=int, default=32)
    result.add_argument('--partition-timeout', type=float, default=20.0,
                        help='Seconds per source solid for the verified planar partition fallback')
    result.add_argument('--no-nonmonotone', action='store_true', help='Disable one bounded non-monotone CSG admission')
    result.add_argument('--nonmonotone-regression', type=float, default=0.12)
    result.add_argument('--max-revolution-atoms', type=int, default=8)
    result.add_argument('--partition-score-min', type=float, default=0.0)
    result.add_argument('--freeform-gate', action='store_true', help='Stop early on freeform-dominant solids')
    result.add_argument('--_worker', action='store_true', help=argparse.SUPPRESS)
    return result


def case_row(report, report_path):
    geometry = report.get('geometry', {})
    raw = geometry.get('raw', {})
    return dict(input=report['input'], input_sha256=report.get('input_sha256'),
        status=report['status'], reason=report.get('decision', {}).get('reason'), report=str(report_path),
        geometry_validated=report.get('geometry_validated'),
        elapsed_seconds=report.get('elapsed_seconds'), worker_wall_seconds=report.get('worker_wall_seconds'),
        chamfer_coordinate_space='source_mm' if report['configuration']['mode'] == 'world' else 'normalized_mm',
        chamfer_mean_mm=geometry.get('chamfer_mean_mm'), chamfer_relative=geometry.get('chamfer_relative'),
        volume_iou=geometry.get('volume_iou'), volume_relative_error=geometry.get('volume_relative_error'),
        reference_volume_mm3=raw.get('reference', {}).get('volume_mm3'),
        candidate_volume_mm3=raw.get('candidate', {}).get('volume_mm3'),
        function_count=report.get('structure', {}).get('function_count'),
        build_lines=report.get('structure', {}).get('build_lines'),
        reconstruction_methods=','.join(f.get('method','analytic_end_cap_prism') for f in report.get('recognition',{}).get('features',[])),
        boolean_operation_count=sum(len(f.get('selected_steps',[])) for f in report.get('recognition',{}).get('features',[])))


def run_case(command, output, configuration, source, timeout):
    """A native crash or a stale checkpoint can never be treated as acceptance."""
    from step2code_direct.pipeline import write_json, file_hash
    started = time.monotonic()
    failure, exit_code = None, None
    with (output/'worker.log').open('w', encoding='utf-8') as log:
        try:
            result = subprocess.run(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=timeout)
            exit_code = result.returncode
            if exit_code:
                failure = 'worker_crash'
        except subprocess.TimeoutExpired:
            failure = 'worker_timeout'
        except OSError as exc:
            failure = 'worker_launch_error'
            log.write(f'\n{type(exc).__name__}: {exc}\n')
    path = output/'report.json'
    try:
        report = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(report, dict):
            raise ValueError('Report must be an object')
    except (OSError, ValueError):
        report = {}
        failure = failure or 'worker_missing_report'
    if report.get('stage') != 'finished' or report.get('status') not in ('train_ready', 'provisional', 'rejected'):
        failure = failure or 'worker_incomplete_report'
    if failure:
        report.update(status='provisional', geometry_validated=None,
                      decision={'status':'provisional', 'reason':failure}, worker_exit_code=exit_code)
    report.update(input=str(source), configuration=configuration,
                  worker_wall_seconds=time.monotonic()-started)
    if not report.get('input_sha256'):
        try:
            report['input_sha256'] = file_hash(source)
        except OSError as exc:
            report['input_sha256'] = None
            report['input_digest_error'] = f'{type(exc).__name__}: {exc}'
    write_json(path, report)
    return report


def main(argv=None):
    argument_parser = parser()
    args = argument_parser.parse_args(argv)
    for value, name in ((args.timeout, 'timeout'), (args.length_tolerance, 'length tolerance'),
                        (args.reconstruction_relative_tolerance, 'reconstruction tolerance'), (args.hybrid_timeout, 'hybrid timeout'),
                        (args.partition_timeout, 'partition timeout')):
        if not math.isfinite(value) or value <= 0:
            argument_parser.error(f'{name} must be positive and finite')
    if args.samples < 256 or min(args.max_candidates, args.max_solids, args.max_hybrid_atoms, args.max_boolean_steps, args.max_boolean_trials, args.hybrid_beam_width, args.max_partition_axes, args.max_partition_parts) < 1 or (args.limit is not None and args.limit < 1):
        argument_parser.error('samples must be >=256; limits must be positive')
    source = args.input.resolve()
    configuration = dict(samples=args.samples, mode=args.validation_mode, recognition={
        'length_tolerance':args.length_tolerance,
        'reconstruction_relative_tolerance':args.reconstruction_relative_tolerance,
        'max_candidates':args.max_candidates, 'max_solids':args.max_solids,
        'enable_hybrid':not args.no_hybrid, 'max_hybrid_atoms':args.max_hybrid_atoms,
        'max_boolean_steps':args.max_boolean_steps, 'max_boolean_trials':args.max_boolean_trials,
        'hybrid_beam_width':args.hybrid_beam_width, 'hybrid_timeout_seconds':args.hybrid_timeout,
        'hybrid_search':args.hybrid_search, 'enable_partition':not args.no_partition,
        'max_partition_axes':args.max_partition_axes, 'max_partition_parts':args.max_partition_parts,
        'partition_timeout_seconds':args.partition_timeout,
        'allow_nonmonotone_search':not args.no_nonmonotone,
        'nonmonotone_regression':args.nonmonotone_regression,
        'max_revolution_atoms':args.max_revolution_atoms,
        'partition_score_min':args.partition_score_min,
        'freeform_gate':args.freeform_gate})
    if args._worker:
        if args.out is None:
            argument_parser.error('worker requires --out')
        from step2code_direct.pipeline import convert_case
        from step2code_direct.geometry import Options
        convert_case(source, args.out, samples=args.samples, mode=args.validation_mode,
                     options=Options(**configuration['recognition']))
        return 0
    if source.is_file() and source.suffix.lower() in ('.step', '.stp'):
        inputs = [source]
    elif source.is_dir():
        paths = source.rglob('*') if args.recursive else source.iterdir()
        inputs = sorted((p for p in paths if p.is_file() and p.suffix.lower() in ('.step', '.stp')),
                        key=lambda p: (str(p.relative_to(source)).casefold(), str(p)))
    else:
        argument_parser.error('input must be a STEP/STP file or directory')
    if args.limit:
        inputs = inputs[:args.limit]
    if not inputs:
        argument_parser.error('no STEP/STP input files')
    output = (args.out or ROOT/'step2code_direct'/'out'/datetime.now().strftime('run-%Y%m%d-%H%M%S-%f')).resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        argument_parser.error(f'output must be new or empty: {output}')
    output.mkdir(parents=True, exist_ok=True)
    from step2code_direct.pipeline import write_json, implementation_fingerprints
    summary = dict(tool='step2scapi-direct', version=__version__, cad_ir_used=False,
        capabilities=CAPABILITIES, validation_mode=args.validation_mode, samples=args.samples,
        configuration=configuration, timeout_seconds=args.timeout, implementation_sha256=implementation_fingerprints(),
        enabled_strategies=['direct_prism'] + ([] if args.no_hybrid else ['hybrid_csg']) +
                           ['surface_analysis', 'revolution_atoms'] + ([] if args.no_partition else ['scored_planar_partition']),
        input_directory=str(source), input_count=len(inputs), completed_count=0, run_status='running', counts={}, cases=[])
    for index, input_path in enumerate(inputs, 1):
        case_output = output/'cases'/f'{index:05d}_{input_path.stem}'
        case_output.mkdir(parents=True)
        command = [sys.executable, '-X', 'utf8', '-m', 'step2code_direct', '--_worker', '--input', str(input_path),
            '--out', str(case_output), '--samples', str(args.samples), '--validation-mode', args.validation_mode,
            '--length-tolerance', str(args.length_tolerance), '--reconstruction-relative-tolerance',
            str(args.reconstruction_relative_tolerance), '--max-candidates', str(args.max_candidates),
            '--max-solids', str(args.max_solids), '--max-hybrid-atoms', str(args.max_hybrid_atoms),
            '--max-boolean-steps', str(args.max_boolean_steps), '--max-boolean-trials', str(args.max_boolean_trials),
            '--hybrid-beam-width', str(args.hybrid_beam_width), '--hybrid-timeout', str(args.hybrid_timeout),
            '--hybrid-search', args.hybrid_search, '--max-partition-axes', str(args.max_partition_axes),
            '--max-partition-parts', str(args.max_partition_parts), '--partition-timeout', str(args.partition_timeout),
            '--nonmonotone-regression', str(args.nonmonotone_regression),
            '--max-revolution-atoms', str(args.max_revolution_atoms),
            '--partition-score-min', str(args.partition_score_min)]
        if args.no_hybrid:
            command.append('--no-hybrid')
        if args.no_partition:
            command.append('--no-partition')
        if args.no_nonmonotone:
            command.append('--no-nonmonotone')
        if args.freeform_gate:
            command.append('--freeform-gate')
        report = run_case(command, case_output, configuration, input_path, args.timeout)
        summary['cases'].append(case_row(report, case_output/'report.json'))
        summary['counts'] = dict(Counter(case['status'] for case in summary['cases']))
        summary['completed_count'] = index
        summary['run_status'] = 'finished' if index == len(inputs) else 'running'
        write_json(output/'summary.json', summary)
        print(f'[{index}/{len(inputs)}] {input_path.name}: {report["status"]} ({report.get("decision", {}).get("reason")})', flush=True)
    with (output/'summary.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summary['cases'][0]))
        writer.writeheader()
        writer.writerows(summary['cases'])
    print(f'Results: {output}', flush=True)
    return 0 if all(row['status'] == 'train_ready' for row in summary['cases']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
