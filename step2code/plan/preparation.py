"""Independent STEP topology preparation. No rounding, fitting or hidden welding."""
from step2code.kernel.execute import execute


def prepare(plan, *, events=None):
    execution=execute(plan,events=events)
    return plan, dict(protocol='step-exact-topology-1',input_hash=plan.sha256,output_hash=plan.sha256,
        adjustments=[],connection_tolerance_mm=1e-6,
        checks=['directed curve continuity','kernel wire self-intersection','kernel profile validity',
                'native region validity','single-solid output contracts'],
        node_signatures=execution.signatures), execution
