"""One isolated conversion case. The typed plan is the sole executable artifact."""
from dataclasses import asdict
from pathlib import Path
import json
import math
import runpy
import time
import traceback
from OCP.BRepCheck import BRepCheck_Analyzer
from step2code.plan import PlanError, dump
from step2code.plan.codec import write_json
from step2code.plan.preparation import prepare
from step2code.kernel.import_step import read_reference
from step2code.kernel.runtime import returned_shapes,compound,export_step
from step2code.kernel.signatures import shape_signature
from step2code.verification.metrics import read_step,solid_count,volume
from step2code.verification.geometry import evaluate
from step2code.verification.coverage import verify_source_coverage
from step2code.codegen import Compiler
from step2code.recognition import recognize, RecognitionOptions, UnsupportedGeometry
from step2code.batch.events import Events
from step2code.batch.identity import implementation_identity,case_identity,file_hash,digest
from step2code.batch.checkpoint import Checkpoints


def convert_case(source, output, *, samples=8192,mode='world',options=None,resume=False,expected_fingerprint=None,hard_timeout=None):
    source=Path(source).resolve();output=Path(output).resolve();options=options or RecognitionOptions()
    if mode!='world':raise ValueError('STEP Plan acceptance uses only the fixed source world frame')
    if output.exists() and not resume and any(p.name!='worker.log' for p in output.iterdir()):
        raise FileExistsError('Case output must be empty')
    output.mkdir(parents=True,exist_ok=True);started=time.monotonic()
    implementation=implementation_identity()
    if expected_fingerprint and implementation['fingerprint']!=expected_fingerprint:
        raise ValueError('Worker implementation differs from fixed run manifest')
    config=dict(samples=samples,mode=mode,recognition=asdict(options),hard_timeout_seconds=hard_timeout)
    identity=case_identity(source,config,implementation)
    checkpoints=Checkpoints(output,identity,resume=resume)
    event_stream=Events(output/'events.jsonl',source.stem,started+hard_timeout if hard_timeout else None)
    def events(**item):
        if item.get('phase')=='recognition.body' and item.get('event')=='start':
            checkpoints.begin(int(item['source_body_id'].split('_')[-1]))
        event_stream(**item)
    report=dict(format='step-report',version=1,input=str(source),output=str(output),outcome='incomplete',
        stage='import',reason_code='worker.running',identity=identity,identity_hash=digest(identity),
        implementation=implementation,artifacts={},checks={},signatures={},
        eligibility=dict(geometry='not_run',serialization='not_run',readability='not_run',parameter_edit='not_run',
                         assembly='unsupported',history='unsupported'),history_recovered=False)
    def stage(name):
        report['stage']=name;report['elapsed_seconds']=time.monotonic()-started
        write_json(output/'report.json',report);events(event='start',phase=name)
    def decision(outcome,reason):
        report.update(outcome=outcome,reason_code=reason)
    try:
        stage('import')
        reference,units=read_reference(source)
        report['signatures']['source']=shape_signature(reference)
        checkpoints.initialize(report['signatures']['source']['solid_count'])
        write_json(output/'source.json',dict(path=str(source),sha256=identity['source_sha256'],units=units,
            import_protocol='ocp-mm-transfer-1',kernel=implementation['dependencies']['cadquery-ocp'],
            topology_snapshot_hash=digest(report['signatures']['source']),topology=report['signatures']['source']))
        report['artifacts']['source']='source.json'
        stage('recognition')
        plan,provenance=recognize(reference,source,options,events=events,checkpoint=checkpoints.save,
                                  restored=checkpoints.restore() if resume else None)
        stage('preparation')
        plan,preparation,execution=prepare(plan,events=events)
        dump(plan,output/'model.plan.json');write_json(output/'preparation.json',preparation)
        write_json(output/'provenance.json',dict(plan_hash=plan.sha256,**provenance))
        report['plan_hash']=plan.sha256
        report['recognition']=dict(features=[dict(solid_index=f['solid_index'],method=f['method']) for f in provenance['features']],
                                   source_solid_count=provenance['source_solid_count'])
        report['signatures']['native']=shape_signature(compound(execution.bodies))
        report['artifacts'].update(plan='model.plan.json',preparation='preparation.json',provenance='provenance.json')
        stage('compile')
        compilation=Compiler(plan,execution).emit()
        code_path=output/'model.scapi.py';code_path.write_text(compilation.code,encoding='utf8')
        write_json(output/'model.symbols.json',compilation.symbols)
        report['structure']=compilation.symbols['structure']
        report['artifacts'].update(code='model.scapi.py',symbols='model.symbols.json')
        report['eligibility']['readability']='pass'
        stage('build')
        namespace=runpy.run_path(str(code_path));wrapped=namespace['build']();bodies=returned_shapes(wrapped)
        if len(bodies)!=len(plan.data['outputs']) or any(solid_count(s)!=1 or not BRepCheck_Analyzer(s).IsValid() or volume(s)<=0 for s in bodies):
            raise PlanError('backend.output_contract','/outputs','Generated build violates body contracts')
        candidate=compound(bodies)
        report['signatures']['generated_bodies']=[shape_signature(s) for s in bodies]
        report['signatures']['wrapper']=shape_signature(candidate)
        stage('coverage')
        coverage=verify_source_coverage(reference,bodies,provenance['output_map'],plan.data['outputs'],tolerance=options.reconstruction_relative_tolerance)
        report['checks']['source_coverage']=coverage
        stage('export')
        export_step(bodies,output/'model.step');restored,_=read_reference(output/'model.step')
        report['artifacts']['step']='model.step'
        report['signatures']['step_readback']=shape_signature(restored)
        # Occupied-space measurements are a view; raw body inventory above stays authoritative.
        from step2code.verification.conditioning import occupied_space
        for label,shape in (('wrapper',candidate),('step_readback',restored),('source',reference)):
            try:report['signatures'][label+'_occupied']=shape_signature(occupied_space(shape))
            except Exception as exc:report['signatures'][label+'_occupied']=dict(error=str(exc))
        stage('serialization')
        roundtrip=evaluate(candidate,restored,samples=samples,mode='world');report['checks']['roundtrip']=roundtrip
        rt=roundtrip['decision']['outcome'];report['eligibility']['serialization']={'accepted':'pass','rejected':'fail','incomplete':'indeterminate'}[rt]
        stage('geometry')
        geometry=evaluate(reference,restored,samples=samples,mode='world');report['checks']['geometry']=geometry
        geo=geometry['decision']['outcome']
        report['eligibility']['geometry']='fail' if coverage['status']=='fail' or geo=='rejected' else (
            'pass' if coverage['status']=='pass' and geo=='accepted' else 'indeterminate')
        if rt=='rejected':decision('rejected','serialization.roundtrip_mismatch')
        elif report['eligibility']['geometry']=='fail':decision('rejected','geometry.source_mismatch')
        elif report['eligibility']['geometry']=='indeterminate' or rt=='incomplete':decision('incomplete','measurement.unreliable')
        else:decision('accepted','geometry.verified')
        write_json(output/'checks.json',dict(plan_hash=plan.sha256,code_sha256=file_hash(code_path),
            export_sha256=file_hash(output/'model.step'),checks=report['checks'],signatures=report['signatures'],eligibility=report['eligibility']))
        report['artifacts']['checks']='checks.json'
    except UnsupportedGeometry as exc:
        decision('incomplete',exc.diagnostics.get('reason_code','search.no_verified_candidate'))
        report.update(error=str(exc),recognition_failure=exc.diagnostics)
    except Exception as exc:
        if report['stage']=='import':decision('invalid_input','import.invalid_topology')
        else:decision('error',getattr(exc,'code',report['stage']+'.failure'))
        report.update(error=str(exc),traceback=traceback.format_exc(limit=10))
        if isinstance(exc,PlanError):report['error_details']=dict(pointer=exc.pointer,**exc.details)
    if report['outcome']!='accepted':checkpoints.fail(report['reason_code'])
    report['elapsed_seconds']=time.monotonic()-started
    report['artifact_hashes']={name:file_hash(output/relative) for name,relative in report['artifacts'].items() if (output/relative).is_file()}
    write_json(output/'report.json',report)
    events(event='end' if report['outcome']=='accepted' else 'fail',phase='case',outcome=report['outcome'],reason_code=report['reason_code'])
    return report



