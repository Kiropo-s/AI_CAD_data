from step2code.plan import Plan, empty_document, node
from step2code.codegen import compile_plan
from step2code.kernel.runtime import returned_shapes


def box_document():
    doc=empty_document();doc['nodes']['box']=node('solid.box',width=2.,depth=3.,height=4.)
    doc['outputs']=[dict(id='body',node='box',expected_kind='solid')]
    return doc


def generated(plan):
    compiled=compile_plan(plan);namespace={}
    exec(compile(compiled.code,'standalone-scapi','exec'),namespace)
    return namespace,returned_shapes(namespace['build']()),compiled
