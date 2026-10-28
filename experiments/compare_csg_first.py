"""Alternate two search modes on fixed inputs, with identical total budgets.

Run as python -m step2code_direct.experiments.compare_csg_first. The default
selection is first 20 plus four close failures and two former import failures.
This is a development/diagnostic set, not a random dataset success estimate.
"""
import argparse
from collections import Counter
from dataclasses import asdict
import csv
from datetime import datetime
import json
from pathlib import Path
import sys

from step2code_direct import __version__, CAPABILITIES
from step2code_direct.cli import ROOT, run_case, case_row
from step2code_direct.geometry import Options
from step2code_direct.pipeline import implementation_fingerprints, write_json
from step2code_direct.compare import compare_runs


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=ROOT/'datasets/abc/step/abc_0000')
    parser.add_argument('--out',type=Path)
    parser.add_argument('--limit',type=int,default=20)
    parser.add_argument('--extra-indices',default='29,71,78,80,83,99')
    parser.add_argument('--timeout',type=float,default=120)
    parser.add_argument('--hybrid-timeout',type=float,default=20)
    args=parser.parse_args(argv)
    files=sorted((p for p in args.input.iterdir() if p.is_file() and p.suffix.lower() in ('.step','.stp')),
                 key=lambda p:(p.name.casefold(),str(p)))
    indices=sorted(set(range(min(args.limit,len(files)))) |
                   {int(i.strip()) for i in args.extra_indices.split(',') if i.strip()})
    if args.limit<1 or any(i<0 or i>=len(files) for i in indices):
        parser.error('Invalid selection indices')
    root=(args.out or ROOT/'step2code_direct/out'/datetime.now().strftime('csg-first-comparison-%Y%m%d-%H%M%S')).resolve()
    if root.exists() and any(root.iterdir()):
        parser.error('Output must be new or empty')
    root.mkdir(parents=True,exist_ok=True)
    fingerprints=implementation_fingerprints()
    summaries={}
    for mode in ('lookahead','csg-first'):
        configuration=dict(samples=8192,mode='world',recognition=asdict(Options(
                           hybrid_search=mode,hybrid_timeout_seconds=args.hybrid_timeout)))
        folder=root/mode;folder.mkdir()
        summaries[mode]=dict(tool='step2scapi-direct',version=__version__,cad_ir_used=False,
            capabilities=CAPABILITIES,enabled_strategies=['direct_prism','hybrid_csg'],
            validation_mode='world',samples=8192,configuration=configuration,
            timeout_seconds=args.timeout,implementation_sha256=fingerprints,
            input_directory=str(args.input.resolve()),input_count=len(indices),completed_count=0,
            run_status='running',selection={'first_n':args.limit,'zero_based_indices':indices,
                'kind':'development_regression_and_targeted_diagnostics'},counts={},cases=[])
        write_json(folder/'summary.json',summaries[mode])
    for ordinal,index in enumerate(indices,1):
        # Alternate which mode goes first to reduce order effects; no concurrent workers.
        modes=('lookahead','csg-first') if ordinal%2 else ('csg-first','lookahead')
        for mode in modes:
            summary=summaries[mode]
            folder=root/mode/'cases'/f'{index+1:05d}_{files[index].stem}';folder.mkdir(parents=True)
            command=[sys.executable,'-B','-X','utf8','-m','step2code_direct','--_worker',
                     '--input',str(files[index].resolve()),'--out',str(folder),
                     '--samples','8192','--validation-mode','world','--hybrid-search',mode,
                     '--hybrid-timeout',str(args.hybrid_timeout)]
            report=run_case(command,folder,summary['configuration'],files[index].resolve(),args.timeout)
            recorded=report.get('implementation_sha256')
            if recorded is not None and recorded!=fingerprints:
                raise RuntimeError('Source changed during comparison; preserve partial results and restart in a new directory')
            summary['cases'].append(case_row(report,folder/'report.json'))
            summary['counts']=dict(Counter(c['status'] for c in summary['cases']))
            summary['completed_count']=len(summary['cases'])
            summary['run_status']='finished' if ordinal==len(indices) else 'running'
            write_json(root/mode/'summary.json',summary)
            print(f'[{ordinal}/{len(indices)}] {index:08d} {mode}: {report["status"]} '
                  f'({report.get("decision",{}).get("reason")}) {report["worker_wall_seconds"]:.2f}s',flush=True)
    for mode,summary in summaries.items():
        with (root/mode/'summary.csv').open('w',newline='',encoding='utf-8-sig') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(summary['cases'][0]))
            writer.writeheader();writer.writerows(summary['cases'])
    comparison=compare_runs(root/'lookahead/summary.json',root/'csg-first/summary.json')
    comparison['selection']=summaries['lookahead']['selection']
    comparison['same_total_budgets']=True
    comparison['same_source_fingerprints']=True
    comparison['notes']=['Both modes use the same pinned compatibility evaluator and importer.',
                         'No result was replaced by a favorable rerun. Failed and timed-out inputs remain in the denominator.',
                         'Selection contains development and targeted inputs; no dataset-wide estimate or controlled speedup claim.']
    write_json(root/'comparison.json',comparison)
    print(f'Results: {root}',flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
