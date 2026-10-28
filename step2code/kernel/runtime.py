def returned_shapes(value, placement=None):
    from simplecadapi import Solid, Part, Assembly, PartBuildResult, AssemblyBuildResult
    from simplecadapi.product.placement import identity_placement
    from step2code.verification.conditioning import transform_shape
    import numpy as np
    if isinstance(value, (PartBuildResult, AssemblyBuildResult)):
        value = value.value
    if isinstance(value, Part):
        value = value.body
    if isinstance(value, Assembly):
        parent = placement or identity_placement()
        return [shape for component in value.components
                for shape in returned_shapes(component.item, parent.compose(component.placement))]
    if isinstance(value, Solid):
        if placement is None or placement == identity_placement():
            return [value.wrapped]
        matrix = np.eye(4)
        matrix[:3, :3] = np.array([placement.x_axis, placement.y_axis, placement.z_axis]).T
        matrix[:3, 3] = placement.origin
        return [transform_shape(value.wrapped, matrix)]
    raise ValueError("build() must return an actual Solid, Part or Assembly")


def compound(shapes):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound
    builder = BRep_Builder()
    result = TopoDS_Compound()
    builder.MakeCompound(result)
    for shape in shapes:
        builder.Add(result, shape)
    return result


def export_step(shapes, path):
    from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    Interface_Static.SetIVal_s("write.surfacecurve.mode", 1)
    Interface_Static.SetIVal_s("write.precision.mode", 0)
    writer = STEPControl_Writer()
    for shape in shapes:
        if writer.Transfer(shape, STEPControl_AsIs) != IFSelect_RetDone:
            raise ValueError("STEP transfer failed")
    if writer.Write(str(path)) != IFSelect_RetDone:
        raise ValueError("STEP write failed")


