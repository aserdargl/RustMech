"""Check portable outputs with the VTK EnSight Gold and VTU readers."""

import numpy as np
import pyvista as pv
import pytest

import mechanical_result_export as export


def test_export_saved_result_round_trip(tmp_path):
    grid = pv.merge([pv.examples.cells.Hexahedron(), pv.examples.cells.QuadraticHexahedron(),
                     pv.examples.cells.Tetrahedron(), pv.examples.cells.QuadraticTetrahedron()],
                    merge_points=False)
    grid.points = grid.points.astype(np.float64) + 0.123456789
    index = np.arange(grid.n_points)
    displacement = np.column_stack((index, -index, index / 2)) / 1000
    grid.point_data["Displacement_mm"] = displacement
    grid.point_data["Displacement_Magnitude_mm"] = np.linalg.norm(displacement, axis=1)
    grid.point_data["Von_Mises_MPa"] = index * 1.25
    grid.point_data["Self_Contact_Pressure_MPa"] = index * 2.5
    grid.point_data["Self_Contact_Shear_MPa"] = index * 0.75
    point_fields = list(grid.point_data)
    grid.point_data["ansys_node_num"] = index + 101
    grid.cell_data["ansys_elem_num"] = np.arange(grid.n_cells) + 201

    run = tmp_path / "Kayıtlı analiz"
    fields_folder = run / "03_Results"
    fields_folder.mkdir(parents=True)
    source = fields_folder / "results_undeformed.vtu"
    grid.save(source)
    assert export.find_mechanical_result(run) == export.find_mechanical_result(fields_folder) == source
    original = source.read_bytes()
    package = export.export_mechanical_result(source, tmp_path)

    gold = pv.read(package / "EnSight" / "mechanical.case")[0]
    np.testing.assert_allclose(gold.points, grid.points, rtol=1e-7)
    for cell_type, cells in grid.cells_dict.items():
        np.testing.assert_array_equal(gold.cells_dict[cell_type], cells)
    assert set(gold.point_data) == set(point_fields)
    for name in point_fields:
        np.testing.assert_allclose(gold.point_data[name], grid.point_data[name], rtol=1e-7)

    reference = pv.read(package / "ParaView" / "results_undeformed.vtu")
    deformed = pv.read(package / "ParaView" / "deformed_shape.vtu")
    np.testing.assert_array_equal(reference.points, grid.points)
    np.testing.assert_array_equal(deformed.points, grid.points + displacement)
    for output in (reference, deformed):
        np.testing.assert_array_equal(output.cells, grid.cells)
        for name in grid.point_data:
            np.testing.assert_array_equal(output.point_data[name], grid.point_data[name])
        np.testing.assert_array_equal(output.cell_data["ansys_elem_num"], grid.cell_data["ansys_elem_num"])

    second = export.export_mechanical_result(source, tmp_path)
    assert second != package
    assert (package / "EnSight" / "mechanical.case").is_file()
    assert source.read_bytes() == original


def test_failed_write_leaves_no_partial_package(tmp_path, monkeypatch):
    grid = pv.examples.cells.Tetrahedron()
    grid.point_data["Displacement_mm"] = np.zeros((grid.n_points, 3))
    grid.point_data["Displacement_Magnitude_mm"] = np.zeros(grid.n_points)
    grid.point_data["Von_Mises_MPa"] = np.ones(grid.n_points)
    source = tmp_path / "results_undeformed.vtu"
    grid.save(source)

    def fail(*_):
        raise OSError("VTU write failed")

    monkeypatch.setattr(export, "_write_vtu", fail)
    with pytest.raises(OSError, match="VTU write failed"):
        export.export_mechanical_result(source, tmp_path)
    assert list(tmp_path.iterdir()) == [source]
