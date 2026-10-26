"""Read STEP with an explicit millimeter target and auditable source unit names."""
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
from OCP.TColStd import TColStd_SequenceOfAsciiString


def read_reference(path):
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone:
        raise ValueError(f'Cannot read STEP: {path}')
    sequences = [TColStd_SequenceOfAsciiString() for _ in range(3)]
    reader.FileUnits(*sequences)
    names = [[sequence.Value(i).ToCString() for i in range(1, sequence.Length()+1)]
             for sequence in sequences]
    reader.SetSystemLengthUnit(1.0)
    if reader.TransferRoots() < 1:
        raise ValueError('STEP contains no transferable roots')
    shape = reader.OneShape()
    if shape.IsNull() or not BRepCheck_Analyzer(shape).IsValid():
        raise ValueError('Invalid reference STEP geometry')
    return shape, dict(source_length_units=names[0] or ['unknown'],
                       source_angle_units=names[1] or ['unknown'],
                       source_solid_angle_units=names[2] or ['unknown'],
                       target_length_unit='mm', system_length_unit_mm=reader.SystemLengthUnit(),
                       conversion='STEPControl unit-aware transfer to explicit 1 mm target',
                       assembly_structure='flattened_world_geometry; hierarchy not recovered')
