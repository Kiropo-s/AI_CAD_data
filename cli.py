"""Command-line entry: conversion batches and source-independent plan tools."""
import argparse
import math
from pathlib import Path
from step2code import __version__

def parser():
    result = argparse.ArgumentParser(description='STEP -> step-plan/1 -> native replay + standalone SCAPI -> CD/IoU/volume')
    result.add_argument('--input', required=True, type=Path, help='STEP/STP file or directory')
    result.add_argument('--out', type=Path, help='New run directory containing cases/ and summary files')
    result.add_argument('--limit', type=int, help='Maximum number of sorted input files')
    result.add_argument('--recursive', action='store_true')
    result.add_argument('--samples', type=int, default=8192, help='Surface points per shape (default: 8192)')
    result.add_argument('--validation-mode', choices=('world',), default='world')
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
    result.add_argument('--resume', action='store_true', help='Resume matching new-format checkpoints in --out')
    result.add_argument('--action', choices=('convert','validate','replay','compile'), default='convert', help='Plan actions load only step-plan/1 JSON')
    result.add_argument('--_fingerprint', help=argparse.SUPPRESS)
    result.add_argument('--_worker', action='store_true', help=argparse.SUPPRESS)
    return result



def main(argv=None):
    p=parser();args=p.parse_args(argv)
    if args.samples<256 or not math.isfinite(args.timeout) or args.timeout<=0:
        p.error('--samples must be >=256; --timeout must be positive and finite')
    from step2code.recognition import RecognitionOptions
    names=RecognitionOptions.__dataclass_fields__
    options=RecognitionOptions(**{name:getattr(args,name) for name in names if hasattr(args,name)})
    if args.limit is not None and args.limit<1:p.error('--limit must be positive')
    if args.action!='convert':
        if args.resume or args._worker:p.error('Plan tools do not support batch flags')
        from step2code.plan import load
        plan=load(args.input)
        if args.action=='validate':
            from step2code.plan.preparation import prepare
            prepare(plan)
            print(f'Valid step-plan/1: {plan.sha256} ({len(plan.data["outputs"])} outputs)')
        elif args.action=='replay':
            from step2code.kernel.execute import execute
            from step2code.kernel.runtime import export_step
            if args.out is None:p.error('replay requires --out model.step')
            if args.out.exists():p.error('Replay output already exists')
            args.out.parent.mkdir(parents=True,exist_ok=True)
            export_step(execute(plan).bodies,args.out)
        else:
            from step2code.codegen import compile_plan
            if args.out is None:p.error('compile requires --out model.scapi.py')
            if args.out.exists():p.error('Compile output already exists')
            args.out.parent.mkdir(parents=True,exist_ok=True)
            args.out.write_text(compile_plan(plan).code,encoding='utf8')
        return 0
    if args._worker:
        if args.out is None:p.error('Worker requires --out')
        from step2code.pipeline import convert_case
        convert_case(args.input,args.out,samples=args.samples,mode=args.validation_mode,options=options,
            resume=args.resume,expected_fingerprint=args._fingerprint,hard_timeout=args.timeout)
        return 0
    from step2code.batch.runner import run
    return run(args,options,p)
