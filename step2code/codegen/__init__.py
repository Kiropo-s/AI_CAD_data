from .scapi import Compiler, Compilation, CODE_STRUCTURE_SPEC, COMPILER_VERSION


def compile_plan(plan):
    """Compile a self-contained plan after independent topology/capability validation."""
    from step2code.plan.preparation import prepare
    _,_,execution=prepare(plan)
    return Compiler(plan,execution).emit()
