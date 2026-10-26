"""Command line driver; each STEP case runs in an isolated, bounded process."""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from step2code import __version__


def parser():
    result = argparse.ArgumentParser(description='STEP -> verified prisms/full revolutions -> readable SCAPI -> CD/IoU/volume verification')
    result.add_argument('--input', required=True, type=Path, help='STEP/STP file or directory')
    result.add_argument('--out', type=Path, help='New run directory containing cases/ and summary files')
    result.add_argument('--limit', type=int, help='Maximum number of sorted input files')
    result.add_argument('--recursive', action='store_true')
    result.add_argument('--samples', type=int, default=8192, help='Surface points per shape (default: 8192)')
    result.add_argument('--validation-mode', choices=('world','similarity'), default='world')
    result.add_argument('--timeout', type=float, default=300, help='Seconds per complete conversion case')
    result.add_argument('--length-tolerance', type=float, default=1e-6)
    result.add_argument('--max-candidates', type=int, default=64)
    result.add_argument('--max-solids', type=int, default=64)
    result.add_argument('--max-revolve-candidates', type=int, default=8)
    result.add_argument('--max-section-faces', type=int, default=16)
    result.add_argument('--max-section-edges', type=int, default=256)
    result.add_argument('--max-spline-poles', type=int, default=256, help='Maximum poles per kernel meridian B-spline curve')
    result.add_argument('--max-csg-candidates', type=int, default=96)
    result.add_argument('--max-csg-steps', type=int, default=32)
    result.add_argument('--max-csg-faces', type=int, default=80)
    result.add_argument('--max-csg-seconds', type=float, default=15.0, help='Cooperative CSG budget; --timeout bounds native calls per case')
    result.add_argument('--max-csg-parts', type=int, default=24)
    result.add_argument('--max-csg-depth', type=int, default=3)
    result.add_argument('--_worker', action='store_true', help=argparse.SUPPRESS)
    return result


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def main(argv=None):
    arg_parser = parser()
    args = arg_parser.parse_args(argv)
    import math
    if args.samples < 256 or not math.isfinite(args.timeout) or args.timeout<=0:
        arg_parser.error('--samples must be >=256 and --timeout must be positive and finite')
    if not math.isfinite(args.length_tolerance) or args.length_tolerance<=0 or min(args.max_candidates, args.max_solids, args.max_revolve_candidates, args.max_section_faces, args.max_section_edges, args.max_spline_poles, args.max_csg_candidates, args.max_csg_steps, args.max_csg_faces, args.max_csg_parts)<1 or not math.isfinite(args.max_csg_seconds) or args.max_csg_seconds<=0:
        arg_parser.error('Tolerance, candidate limit and solid limit must be positive')
    if args.max_csg_depth<0 or args.max_csg_depth>3:
        arg_parser.error('--max-csg-depth must be between 0 and 3')
    if args.limit is not None and args.limit<1:
        arg_parser.error('--limit must be positive')
    source = args.input.resolve()
    if args._worker:
        from step2code.pipeline import convert_case
        from step2code.recognition import RecognitionOptions
        if args.out is None:
            arg_parser.error('Worker requires --out')
        convert_case(source, args.out, samples=args.samples, mode=args.validation_mode,
                     options=RecognitionOptions(length_tolerance=args.length_tolerance,
                         max_candidates=args.max_candidates, max_solids=args.max_solids,
                         max_revolve_candidates=args.max_revolve_candidates, max_section_faces=args.max_section_faces,
                         max_section_edges=args.max_section_edges, max_spline_poles=args.max_spline_poles,
                         max_csg_candidates=args.max_csg_candidates, max_csg_steps=args.max_csg_steps,
                         max_csg_faces=args.max_csg_faces, max_csg_seconds=args.max_csg_seconds,
                         max_csg_parts=args.max_csg_parts, max_csg_depth=args.max_csg_depth))
        return 0
    if source.is_file() and source.suffix.lower() in ('.step','.stp'):
        inputs = [source]
    elif source.is_dir():
        iterator = source.rglob('*') if args.recursive else source.iterdir()
        inputs = sorted((p for p in iterator if p.is_file() and p.suffix.lower() in ('.step','.stp')),
                        key=lambda p:str(p).lower())
    else:
        arg_parser.error('Input must be an existing STEP/STP file or directory')
    inputs = inputs[:args.limit] if args.limit else inputs
    if not inputs:
        arg_parser.error('No STEP/STP files found')
    if len({p.stem.casefold() for p in inputs}) != len(inputs):
        arg_parser.error('Duplicate input stems; split this run into separate output directories')
    output = (args.out or Path(__file__).parent/'out'/datetime.now().strftime('run-%Y%m%d-%H%M%S-%f')).resolve()
    if output.exists() and not output.is_dir():
        arg_parser.error(f'Output path is not a directory: {output}')
    if output.exists() and any(output.iterdir()):
        arg_parser.error(f'Output directory must be empty; existing artifacts are preserved: {output}')
    output.mkdir(parents=True, exist_ok=True)
    entry = Path(__file__).with_name('step2scapi.py')
    summaries = []
    for index, path in enumerate(inputs, 1):
        case = output/'cases'/path.stem
        case.mkdir(parents=True)
        command = [sys.executable, str(entry), '--_worker', '--input', str(path), '--out', str(case),
                   '--samples', str(args.samples), '--validation-mode', args.validation_mode,
                   '--length-tolerance', str(args.length_tolerance),
                   '--max-candidates', str(args.max_candidates), '--max-solids', str(args.max_solids),
                   '--max-revolve-candidates', str(args.max_revolve_candidates),
                   '--max-section-faces', str(args.max_section_faces), '--max-section-edges', str(args.max_section_edges),
                   '--max-spline-poles', str(args.max_spline_poles),
                   '--max-csg-candidates', str(args.max_csg_candidates), '--max-csg-steps', str(args.max_csg_steps),
                   '--max-csg-faces', str(args.max_csg_faces), '--max-csg-seconds', str(args.max_csg_seconds),
                   '--max-csg-parts', str(args.max_csg_parts), '--max-csg-depth', str(args.max_csg_depth)]
        process_error = None
        case_started = time.monotonic()
        with (case/'worker.log').open('w', encoding='utf-8') as log:
            try:
                completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                           timeout=args.timeout, cwd=entry.parent.parent)
                if completed.returncode != 0:
                    process_error = f'Worker exited with code {completed.returncode}'
            except subprocess.TimeoutExpired:
                process_error = f'Case timeout after {args.timeout:g} seconds'
            except OSError as exc:
                process_error = f'Cannot launch worker: {exc}'
        report_path = case/'report.json'
        try:
            report = json.loads(report_path.read_text(encoding='utf-8'))
        except (OSError,ValueError):
            report = {'input': str(path), 'stage': 'worker'}
        if process_error or report.get('status') not in ('train_ready','rejected','provisional'):
            report.update(status='provisional', decision={'status':'provisional','reason':'worker_error'},
                          error=process_error or 'Worker did not produce a completed report',
                          elapsed_seconds=time.monotonic()-case_started, geometry_validated=None,
                          training_task='geometry_reconstruction', history_recovered=False,
                          parameter_edit_validation={'status':'not_run'}, assembly_validation={'status':'unsupported'})
            _write_json(report_path, report)
        geometry = report.get('geometry', {})
        raw = geometry.get('raw', {})
        row = {'input': str(path), 'status':report['status'],
               'reason':report['decision']['reason'], 'report':str(report_path),
               'code_structure_spec':report.get('code_structure_spec'),
               'geometry_validated':report.get('geometry_validated'),
               'source_coverage':report.get('source_coverage_validation',{}).get('status','not_run'),
               'parameter_edit_status':report.get('parameter_edit_validation',{}).get('status','not_run'),
               'assembly_status':report.get('assembly_validation',{}).get('status','not_run'),
               'template_count':report.get('structure',{}).get('template_count'),
               'instance_count':report.get('structure',{}).get('instance_count'),
               'function_count':report.get('structure',{}).get('function_count'),
               'build_lines':report.get('structure',{}).get('build_lines'),
               'chamfer_coordinate_space':'source_mm' if args.validation_mode=='world' else 'normalized_mm',
               'chamfer_mean_mm':geometry.get('chamfer_mean_mm'),
               'chamfer_relative':geometry.get('chamfer_relative'),
               'volume_iou':geometry.get('volume_iou'),
               'reference_volume_mm3':raw.get('reference',{}).get('volume_mm3',geometry.get('reference_volume_mm3')),
               'candidate_volume_mm3':raw.get('candidate',{}).get('volume_mm3',geometry.get('candidate_volume_mm3')),
               'volume_error_space':'source_mm3' if args.validation_mode=='world' else 'normalized_mm3',
               'volume_relative_error':geometry.get('volume_relative_error'),
               'elapsed_seconds':report.get('elapsed_seconds')}
        summaries.append(row)
        print(f'[{index}/{len(inputs)}] {path.stem}: {row["status"]} ({row["reason"]})', flush=True)
        _write_json(output/'summary.json', {'tool':'step2scapi','version':__version__,
                    'validation_mode':args.validation_mode,'samples':args.samples,
                    'counts':dict(Counter(r['status'] for r in summaries)), 'cases':summaries})
    with (output/'summary.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summaries[0]))
        writer.writeheader(); writer.writerows(summaries)
    counts = dict(Counter(r['status'] for r in summaries))
    print(f'Completed: {counts}. Reports: {output}', flush=True)
    return 0 if all(row['status']=='train_ready' for row in summaries) else 1
