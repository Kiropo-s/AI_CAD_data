"""Parent-owned manifests and hard process deadlines; partial batches remain explicit."""
from collections import Counter
from dataclasses import asdict
from datetime import datetime
import csv
import json
from pathlib import Path
import subprocess
import sys
import time
from step2code.plan.codec import write_json
from .identity import implementation_identity,file_hash,digest
from .process import terminate,isolation_options
from .diagnostics import write_case_sidecars


def summary(output,manifest,reports):
    manifest['completed_count']=len(reports)
    manifest['outcome_counts']=dict(Counter(r['outcome'] for r in reports))
    write_json(output/'manifest.json',manifest)
    rows=[dict(case_id=Path(r['input']).stem,outcome=r['outcome'],reason_code=r['reason_code'],stage=r['stage'],
        elapsed_seconds=r['elapsed_seconds'],report=str(Path(r['output'])/'report.json')) for r in reports]
    write_json(output/'summary.json',dict(run_state=manifest['run_state'],requested_count=manifest['requested_count'],
        selected_count=manifest['selected_count'],completed_count=len(reports),counts=manifest['outcome_counts'],cases=rows))
    temp=output/'cases.csv.tmp'
    with temp.open('w',newline='',encoding='utf-8-sig') as stream:
        fields=['case_id','outcome','reason_code','stage','elapsed_seconds','report']
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    temp.replace(output/'cases.csv')


def run(args,options,parser):
    source=args.input.resolve()
    if source.is_file() and source.suffix.lower() in ('.step','.stp'):inputs=[source]
    elif source.is_dir():
        paths=source.rglob('*') if args.recursive else source.iterdir()
        inputs=sorted((p for p in paths if p.is_file() and p.suffix.lower() in ('.step','.stp')),key=lambda p:str(p).lower())
    else:parser.error('Input must be a STEP/STP file or directory')
    available=len(inputs);inputs=inputs[:args.limit] if args.limit else inputs
    if not inputs:parser.error('No STEP inputs found')
    if len({p.stem.casefold() for p in inputs})!=len(inputs):parser.error('Duplicate input stems')
    output=(args.out or Path(__file__).resolve().parents[1]/'out'/datetime.now().strftime('run-%Y%m%d-%H%M%S-%f')).resolve()
    if args.resume and not args.out:parser.error('--resume requires --out')
    if not args.resume and output.exists() and any(output.iterdir()):parser.error('Output directory must be empty')
    output.mkdir(parents=True,exist_ok=True)
    implementation=implementation_identity()
    config=dict(samples=args.samples,mode=args.validation_mode,recognition=asdict(options),hard_timeout_seconds=args.timeout)
    selected=[dict(path=str(p),sha256=file_hash(p)) for p in inputs]
    manifest=dict(format='step-run',version=1,created_at=datetime.now().astimezone().isoformat(),run_state='running',
        implementation=implementation,configuration=config,available_count=available,
        requested_count=args.limit or available,selected_count=len(inputs),completed_count=0,inputs=selected)
    key=digest(dict(implementation=implementation,configuration=config,inputs=selected))
    manifest['identity_hash']=key
    if args.resume:
        previous=json.loads((output/'manifest.json').read_text(encoding='utf8'))
        if previous['identity_hash']!=key:parser.error('Resume identity mismatch: source, code, dependencies or configuration changed')
        manifest=previous;manifest['run_state']='running'
    reports=[];active=None;process=None
    summary(output,manifest,reports)
    try:
        for index,path in enumerate(inputs,1):
            active=output/'cases'/path.stem;active.mkdir(parents=True,exist_ok=True)
            report_path=active/'report.json'
            previous=None
            if args.resume and report_path.exists():
                previous=json.loads(report_path.read_text(encoding='utf8'))
                if previous['outcome'] in ('accepted','rejected','invalid_input'):
                    if any(not (active/previous['artifacts'][name]).is_file() or file_hash(active/previous['artifacts'][name])!=sha
                           for name,sha in previous.get('artifact_hashes',{}).items()):
                        raise ValueError('Resume artifact hash mismatch: '+str(active))
                    previous=write_case_sidecars(path,active,previous)
                    write_json(report_path,previous)
                    reports.append(previous);summary(output,manifest,reports);continue
                archive=active/'work'/'attempts';archive.mkdir(parents=True,exist_ok=True)
                write_json(archive/f'report_{time.time_ns()}.json',previous)
            entry=Path(__file__).resolve().parents[1]/'step2scapi.py'
            command=[sys.executable,str(entry),'--_worker','--input',str(path),'--out',str(active),
                '--samples',str(args.samples),'--timeout',str(args.timeout),'--_fingerprint',implementation['fingerprint']]
            for name,value in asdict(options).items():
                if name=='reconstruction_relative_tolerance':continue
                command.extend(['--'+name.replace('_','-'),str(value)])
            if args.resume:command.append('--resume')
            started=time.monotonic();failure=None
            with (active/'worker.log').open('a' if args.resume else 'w',encoding='utf8') as log:
                process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,cwd=entry.parent.parent,**isolation_options())
                try:
                    process.wait(timeout=args.timeout)
                    if process.returncode:failure=('worker.crash',f'Exit code {process.returncode}')
                except subprocess.TimeoutExpired:
                    terminate(process);failure=('worker.timeout',f'Hard timeout {args.timeout:g}s')
            if report_path.exists():report=json.loads(report_path.read_text(encoding='utf8'))
            else:report=dict(input=str(path),output=str(active),stage='worker',artifacts={})
            if failure:
                state_path=active/'case-state.json'
                if state_path.exists():
                    state=json.loads(state_path.read_text(encoding='utf8'))
                    body=state.get('active_body')
                    if body and state['body_states'].get(str(body),{}).get('state')!='complete':
                        state['body_states'][str(body)]=dict(state='unknown',reason_code=failure[0])
                    write_json(state_path,state)
                report.update(outcome='incomplete' if failure[0]=='worker.timeout' else 'error',reason_code=failure[0],
                    error=failure[1],elapsed_seconds=time.monotonic()-started)
                write_json(report_path,report)
            report.setdefault('input',str(path))
            report.setdefault('output',str(active))
            report=write_case_sidecars(path,active,report)
            write_json(report_path,report)
            reports.append(report);summary(output,manifest,reports)
            print(f'[{index}/{len(inputs)}] {path.stem}: {report["outcome"]} ({report["reason_code"]}, {report["elapsed_seconds"]:.1f}s)',flush=True)
            active=None;process=None
        manifest['run_state']='completed';summary(output,manifest,reports)
    except KeyboardInterrupt:
        if process and process.poll() is None:terminate(process)
        manifest['run_state']='interrupted';summary(output,manifest,reports)
        return 130
    except BaseException:
        if process and process.poll() is None:process.kill();process.wait()
        manifest['run_state']='failed';summary(output,manifest,reports)
        raise
    print('Completed: '+str(output),flush=True)
    return 0
