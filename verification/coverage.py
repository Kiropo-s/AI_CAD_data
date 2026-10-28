"""Coverage is an independent acceptance gate, never an execution dependency."""
import math
from OCP.TopAbs import TopAbs_SOLID, TopAbs_FACE
from step2code.kernel.signatures import explore
from .metrics import volume, symmetric_difference_volume, solid_count


def verify_source_coverage(reference, bodies, output_map, outputs, *, tolerance=1e-7):
    sources=list(explore(reference,TopAbs_SOLID));checks=[]
    if len(sources)!=len(bodies) or len(output_map)!=len(sources) or len(outputs)!=len(sources):
        return dict(status='fail',reason_code='coverage.body_count')
    if [m['source_solid_index'] for m in output_map]!=list(range(1,len(sources)+1)):
        return dict(status='fail',reason_code='coverage.source_mapping')
    for i,(source,body,mapping,output) in enumerate(zip(sources,bodies,output_map,outputs),1):
        faces=sum(1 for _ in explore(source,TopAbs_FACE))
        if mapping['body_id']!=output['id'] or mapping['output_node']!=output['node'] or mapping['source_face_indices']!=list(range(1,faces+1)):
            return dict(status='fail',reason_code='coverage.face_mapping',body_index=i)
        try:
            error=symmetric_difference_volume(source,body)/volume(source)
            volume_error=abs(volume(body)-volume(source))/volume(source)
            valid=solid_count(body)==1 and math.isfinite(error) and error<=tolerance and volume_error<=tolerance
            checks.append(dict(body_id=output['id'],symmetric_difference_relative=error,volume_relative_error=volume_error,status='pass' if valid else 'fail'))
        except Exception as exc:
            checks.append(dict(body_id=output['id'],status='indeterminate',error=str(exc)))
    status='fail' if any(c['status']=='fail' for c in checks) else ('indeterminate' if any(c['status']=='indeterminate' for c in checks) else 'pass')
    return dict(status=status,reason_code='coverage.'+('complete' if status=='pass' else status),bodies=checks)
