"""Fail-fast validation of every value, dimension and edge in the execution DAG."""
import math
import re
from .model import PlanError, arguments
from .registry import OPS, PARAMETER_TYPES
from .graph import topological_order, reachable

ID = re.compile(r'^[A-Za-z][A-Za-z0-9_]*$')


def fail(code, pointer, text):
    raise PlanError(code, pointer, text)


def fields(value, expected, pointer):
    if not isinstance(value,dict):
        fail('schema.fields', pointer, 'Expected object')
    missing=set(expected)-set(value);extra=set(value)-set(expected)
    if missing or extra:
        field=sorted(missing or extra)[0]
        fail('schema.fields', pointer+'/'+field.replace('~','~0').replace('/','~1'),
             f'Expected exactly {sorted(expected)}')


def number(value):
    try:return type(value) in (int,float) and math.isfinite(value)
    except OverflowError:return False


def check_value(value, kind, pointer):
    if kind=='number':
        valid=number(value)
    elif kind=='integer':
        valid=type(value) is int
    elif kind=='boolean':
        valid=type(value) is bool
    elif kind=='winding':
        valid=value in ('cw','ccw')
    elif kind in ('point2','point3','direction3','interval'):
        size=2 if kind in ('point2','interval') else 3
        valid=isinstance(value,list) and len(value)==size and all(number(v) for v in value)
        if valid and kind=='direction3':
            valid=abs(sum(v*v for v in value)-1)<=1e-10
        if valid and kind=='interval':
            valid=value[0]<value[1]
    elif kind in ('numbers','integers'):
        valid=isinstance(value,list) and len(value)>0 and all(
            number(v) if kind=='numbers' else type(v) is int for v in value)
    elif kind=='points2':
        valid=isinstance(value,list) and len(value)>=2
        if valid:
            for i,item in enumerate(value):
                check_value(item,'point2',pointer+'/'+str(i))
    else:
        valid=False
    if not valid:
        fail('type.value',pointer,f'Expected finite {kind}')


def validate(doc):
    def json_values(value, pointer='', depth=0):
        if depth>64:
            fail('json.depth_limit',pointer,'Maximum JSON nesting is 64')
        if isinstance(value,dict):
            for k,v in value.items():
                if not isinstance(k,str):fail('json.key',pointer,'Keys must be strings')
                json_values(v,pointer+'/'+k,depth+1)
        elif isinstance(value,list):
            for i,v in enumerate(value):json_values(v,pointer+'/'+str(i),depth+1)
        elif type(value) is float and not math.isfinite(value):
            fail('json.nonfinite',pointer,'Nonfinite number')
        elif value is not None and type(value) not in (str,bool,int,float):
            fail('json.type',pointer,'Not a JSON value')
    json_values(doc)
    if not isinstance(doc,dict) or doc.get('format')!='step-plan':
        fail('schema.unsupported_format','/format','Expected step-plan')
    if type(doc.get('version')) is not int or doc['version']!=1:
        fail('schema.unsupported_version','/version','Expected integer 1')
    fields(doc,('format','version','units','parameters','nodes','outputs'),'')
    if doc['units']!={'length':'mm','angle':'rad'}:
        fail('schema.units','/units','Expected mm and rad')
    for table in ('nodes','parameters'):
        if not isinstance(doc[table],dict):fail('schema.table','/'+table,'Expected object')
        for key in doc[table]:
            if not ID.fullmatch(key):fail('schema.id','/'+table+'/'+key,'Invalid identifier')
    if not doc['nodes']:fail('schema.empty_graph','/nodes','No nodes')
    for key,p in doc['parameters'].items():
        ptr='/parameters/'+key
        fields(p,('type','unit','value','editability'),ptr)
        if not isinstance(p['type'],str) or p['type'] not in PARAMETER_TYPES:fail('type.parameter',ptr+'/type','Unknown parameter type')
        if p['unit'] not in ('mm','rad','1'):fail('type.unit',ptr+'/unit','Unknown unit')
        if p['editability'] not in ('independent','fixed'):fail('parameter.editability',ptr,'Unknown editability')
        check_value(p['value'],p['type'],ptr+'/value')
    used=set()
    for key,n in doc['nodes'].items():
        ptr='/nodes/'+key
        fields(n,('op','inputs','args'),ptr)
        if not isinstance(n['op'],str) or n['op'] not in OPS:fail('schema.unknown_op',ptr+'/op','Unknown operation')
        spec=OPS[n['op']]
        fields(n['inputs'],spec.inputs,ptr+'/inputs')
        fields(n['args'],spec.args,ptr+'/args')
        for role,(kind,minimum,maximum) in spec.inputs.items():
            refs=n['inputs'][role]
            rptr=ptr+'/inputs/'+role
            if not isinstance(refs,list) or len(refs)<minimum or (maximum is not None and len(refs)>maximum):
                fail('type.input_arity',rptr,'Wrong number of inputs')
            for i,ref in enumerate(refs):
                if not isinstance(ref,str) or ref not in doc['nodes']:
                    fail('schema.missing_reference',rptr+'/'+str(i),str(ref))
                target=doc['nodes'][ref]
                if not isinstance(target,dict) or not isinstance(target.get('op'),str) or target['op'] not in OPS:
                    fail('schema.unknown_op','/nodes/'+ref,'Invalid dependency')
                if OPS[target['op']].result!=kind:
                    fail('type.input_mismatch',rptr+'/'+str(i),f'Expected {kind}')
        for name,sig in spec.args.items():
            binding=n['args'][name];bptr=ptr+'/args/'+name
            if not isinstance(binding,dict) or set(binding) not in ({'literal'},{'param'}):
                fail('schema.binding',bptr,'Expected literal or param binding')
            if 'param' in binding:
                ref=binding['param']
                if not isinstance(ref,str) or ref not in doc['parameters']:
                    fail('parameter.missing',bptr+'/param',str(ref))
                p=doc['parameters'][ref];used.add(ref)
                if (p['type'],p['unit'])!=(sig.type,sig.unit):
                    fail('type.parameter_dimension',bptr,f'Expected {sig.type}/{sig.unit}')
                value=p['value']
            else:
                value=binding['literal']
            check_value(value,sig.type,bptr)
            if sig.rule=='positive' and value<=0:fail('geometry.range',bptr,'Must be positive')
            if sig.rule=='nonnegative' and value<0:fail('geometry.range',bptr,'Must be nonnegative')
        a=arguments(doc,n);op=n['op']
        if op=='curve.line2' and math.dist(a['start'],a['end'])<=1e-12:
            fail('geometry.degenerate',ptr,'Coincident line endpoints')
        if op=='curve.arc2' and not 0<abs(a['sweep_angle'])<2*math.pi:
            fail('geometry.arc_sweep',ptr+'/args/sweep_angle','Use circle2 for a full circle')
        if op=='curve.ellipse2' and a['minor_radius']>a['major_radius']:
            fail('geometry.ellipse_axes',ptr,'Major radius must be at least minor radius')
        if op=='frame.rigid3' and abs(sum(x*y for x,y in zip(a['x_axis'],a['y_axis'])))>1e-10:
            fail('geometry.frame',ptr,'Axes must be orthogonal')
        if op=='solid.revolve' and abs(a['angle']-2*math.pi)>1e-12:
            fail('capability.revolve_angle',ptr+'/args/angle','Only full revolution is supported')
        if op=='solid.cone' and a['radius_bottom']+a['radius_top']==0:
            fail('geometry.degenerate',ptr,'Both radii are zero')
        if op=='solid.torus' and a['major_radius']<=a['minor_radius']:
            fail('geometry.torus',ptr,'Major radius must exceed minor radius')
        if op=='curve.bspline2':
            p,k,m,w=a['poles'],a['knots'],a['multiplicities'],a['weights'];d=a['degree']
            if not 1<=d<=25 or len(p)>256 or len(p)<d+1 or len(k)!=len(m) or len(p)!=len(w):
                fail('geometry.bspline',ptr,'Inconsistent spline dimensions or limits')
            if any(v<=0 for v in w) or any(not 1<=v<=d+1 for v in m) or any(x>=y for x,y in zip(k,k[1:])):
                fail('geometry.bspline',ptr,'Invalid weights, multiplicities or knot ordering')
            if not a['periodic'] and sum(m)!=len(p)+d+1:
                fail('geometry.bspline',ptr,'Nonperiodic knot multiplicity sum is inconsistent')
            if a['periodic'] and (m[0]!=m[-1] or sum(m[:-1])!=len(p)):
                fail('geometry.bspline',ptr,'Periodic multiplicities are inconsistent')
            lo,hi=(k[0],k[-1]) if a['periodic'] else (k[0],k[-1])
            if not lo<=a['trim'][0]<a['trim'][1]<=hi:
                fail('geometry.bspline_domain',ptr+'/args/trim','Trim exceeds knot domain')
    order=topological_order(doc['nodes'])
    if not isinstance(doc['outputs'],list) or not doc['outputs']:
        fail('schema.outputs','/outputs','At least one explicit output is required')
    ids=set();roots=[]
    for i,o in enumerate(doc['outputs']):
        ptr='/outputs/'+str(i);fields(o,('id','node','expected_kind'),ptr)
        if not isinstance(o['id'],str) or not ID.fullmatch(o['id']) or o['id'] in ids:
            fail('schema.output_id',ptr+'/id','Invalid or duplicate output ID')
        ids.add(o['id'])
        if not isinstance(o['node'],str) or o['node'] not in doc['nodes']:
            fail('schema.missing_reference',ptr+'/node','Missing output root')
        if o['expected_kind']!='solid' or OPS[doc['nodes'][o['node']]['op']].result!='Region3':
            fail('type.output',ptr,'Output must be Region3 with solid contract')
        roots.append(o['node'])
    orphan=set(doc['nodes'])-reachable(doc['nodes'],roots)
    if orphan:fail('graph.unreachable','/nodes/'+sorted(orphan)[0],'Unreachable node')
    unused=set(doc['parameters'])-used
    if unused:fail('parameter.unused','/parameters/'+sorted(unused)[0],'Unused parameter')
    return order
