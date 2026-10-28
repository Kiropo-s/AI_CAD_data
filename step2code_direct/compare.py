"""Compare saved runs by filename AND source SHA256; never rerun either route."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def read_case_report(summary_path, row):
    value = row.get('report')
    if not value:
        return {}
    path = Path(value)
    candidates = [path] if path.is_absolute() else [summary_path.parent/path, ROOT/path]
    for candidate in candidates:
        if candidate.is_file():
            return read_json(candidate)
    raise ValueError(f'Missing case report: {value}')


def load_run(summary_path):
    summary_path = Path(summary_path).resolve()
    summary = read_json(summary_path)
    if summary.get('run_status') not in (None, 'finished'):
        raise ValueError(f'Run is incomplete: {summary_path}')
    if not summary.get('cases'):
        raise ValueError(f'Run has no cases: {summary_path}')
    cases = {}
    for row in summary['cases']:
        report = read_case_report(summary_path, row)
        digest = row.get('input_sha256') or report.get('input_sha256')
        if not digest:
            raise ValueError(f'Missing recorded source SHA256: {row.get("input")}')
        if not isinstance(digest,str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest.lower()):
            raise ValueError('Invalid recorded SHA256')
        if row.get('input_sha256') and report.get('input_sha256') and row['input_sha256'] != report['input_sha256']:
            raise ValueError('Summary/report source SHA256 mismatch')
        if report.get('status') and row['status'] != report['status']:
            raise ValueError('Summary/report status mismatch')
        if row['status'] not in ('train_ready','provisional','rejected'):
            raise ValueError('Summary contains an incomplete/unknown case status')
        # Never hash today's source to manufacture evidence for a historical run.
        key = (Path(row['input']).name.casefold(), digest)
        if key in cases:
            raise ValueError(f'Ambiguous duplicate filename/SHA256: {key[0]}')
        config = report.get('configuration') or summary.get('configuration') or {}
        settings = dict(mode=config.get('mode', summary.get('validation_mode')),
                        samples=config.get('samples', summary.get('samples')))
        geometry = report.get('geometry', {})
        cases[key] = dict(row=row, report=report, settings=settings, thresholds=geometry.get('thresholds'),
                          runtime=report.get('runtime_versions'),
                          implementation=report.get('implementation_sha256', {}))
    return summary, cases


def compare_runs(baseline_path, direct_path):
    baseline, first = load_run(baseline_path)
    direct, second = load_run(direct_path)
    if direct.get('cad_ir_used') is not False:
        raise ValueError('--direct must be a direct-conversion summary with cad_ir_used=false')
    shared = sorted(set(first) & set(second))
    if not shared:
        raise ValueError('No matching filename AND source SHA256 pairs')
    rows = []
    for key in shared:
        a, b = first[key], second[key]
        ar, br = a['row'], b['row']
        known_settings = all(v is not None for v in a['settings'].values()) and all(v is not None for v in b['settings'].values())
        settings_equal = known_settings and a['settings'] == b['settings']
        thresholds_equal = (a['thresholds'] == b['thresholds']) if a['thresholds'] and b['thresholds'] else None
        runtime_equal = (a['runtime'] == b['runtime']) if a['runtime'] and b['runtime'] else None
        shared_modules = ('step2code/step_import.py', 'step2code/validation.py', 'json2scapi/runtime/worker.py',
                          'json2scapi/evaluation/metrics.py', 'json2scapi/evaluation/policy.py',
                          'json2scapi/evaluation/registration.py', 'json2scapi/evaluation/similarity.py')
        aliases = {'step2code/step_import.py': 'step2code_direct/compat/import_step.py',
                   'step2code/validation.py': 'step2code_direct/compat/validation.py'}
        def fingerprint(run, name):
            return run['implementation'].get(name) or run['implementation'].get(aliases.get(name))
        versions = [(fingerprint(a, name), fingerprint(b, name)) for name in shared_modules]
        evaluator_equal = all(x == y for x, y in versions) if all(x and y for x, y in versions) else None
        row = dict(input=br['input'], input_sha256=key[1], baseline_status=ar['status'], direct_status=br['status'],
            baseline_reason=ar.get('reason'), direct_reason=br.get('reason'),
            same_measurement_settings=settings_equal, same_thresholds=thresholds_equal,
            same_runtime=runtime_equal, same_shared_implementation=evaluator_equal,
            baseline_report=ar.get('report'), direct_report=br.get('report'))
        for metric in ('chamfer_relative', 'volume_iou', 'volume_relative_error', 'elapsed_seconds', 'function_count', 'build_lines'):
            row['baseline_'+metric] = ar.get(metric)
            row['direct_'+metric] = br.get(metric)
        rows.append(row)
    count = len(shared)
    first_counts = Counter(row['baseline_status'] for row in rows)
    second_counts = Counter(row['direct_status'] for row in rows)
    delta = (second_counts['train_ready']-first_counts['train_ready'])/count
    missing = lambda keys: [dict(filename=k[0], input_sha256=k[1]) for k in sorted(keys)]
    notices = ['This is a descriptive comparison. Independent recognizers and capability differences prevent attributing a change solely to removing IR.',
               'Elapsed time is reported without claiming speedup: historical runs may differ in hardware, load or cache state.']
    if any(not row['same_measurement_settings'] for row in rows):
        notices.append('Measurement settings are missing or differ; acceptance rates are not a controlled comparison.')
    if any(row['same_shared_implementation'] is None for row in rows):
        notices.append('A run lacks shared implementation fingerprints; equal evaluator versions cannot be established.')
    if any(row['same_shared_implementation'] is False for row in rows):
        notices.append('Shared import/runtime/metric implementation changed between runs.')
    if any(row['same_runtime'] is not True for row in rows):
        notices.append('Runtime versions differ or are unavailable for at least one pair.')
    result = dict(baseline_summary=str(Path(baseline_path).resolve()), direct_summary=str(Path(direct_path).resolve()),
        inference='descriptive_comparison_only', matched_count=count, baseline_total=len(first), direct_total=len(second),
        baseline_counts=dict(first_counts), direct_counts=dict(second_counts),
        baseline_acceptance_rate=first_counts['train_ready']/count,
        direct_acceptance_rate=second_counts['train_ready']/count,
        acceptance_rate_difference_percentage_points=100*delta,
        direct_only_accepted=sum(r['direct_status']=='train_ready' and r['baseline_status']!='train_ready' for r in rows),
        baseline_only_accepted=sum(r['baseline_status']=='train_ready' and r['direct_status']!='train_ready' for r in rows),
        baseline_capabilities=baseline.get('capabilities', 'not_declared'), direct_capabilities=direct.get('capabilities'),
        status_matrix=dict(Counter(r['baseline_status']+' -> '+r['direct_status'] for r in rows)),
        unmatched_baseline=missing(set(first)-set(second)), unmatched_direct=missing(set(second)-set(first)),
        notices=notices, cases=rows)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True, help='Existing STEP->IR/plan->code summary.json')
    parser.add_argument('--direct', type=Path, required=True, help='Existing direct route summary.json')
    parser.add_argument('--out', type=Path, required=True, help='New or empty comparison directory')
    args = parser.parse_args(argv)
    output = args.out.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        parser.error('comparison output must be new or empty')
    try:
        result = compare_runs(args.baseline, args.direct)
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    from step2code_direct.pipeline import write_json
    output.mkdir(parents=True, exist_ok=True)
    write_json(output/'comparison.json', result)
    with (output/'comparison.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(result['cases'][0]))
        writer.writeheader()
        writer.writerows(result['cases'])
    lines = ['# Direct STEP conversion comparison', '', f'Matched source files: {result["matched_count"]}', '',
             '| Route | Accepted | Matched acceptance rate |', '| --- | ---: | ---: |',
             f'| Baseline | {result["baseline_counts"].get("train_ready",0)} | {result["baseline_acceptance_rate"]:.2%} |',
             f'| Direct | {result["direct_counts"].get("train_ready",0)} | {result["direct_acceptance_rate"]:.2%} |', '',
             f'Difference: {result["acceptance_rate_difference_percentage_points"]:+.2f} percentage points.', '',
             f'Unmatched baseline/direct: {len(result["unmatched_baseline"])}/{len(result["unmatched_direct"])}.', '',
             *['- '+notice for notice in result['notices']], '']
    (output/'COMPARISON.md').write_text('\n'.join(lines), encoding='utf-8')
    print(f'Compared {result["matched_count"]} files; saved {output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
