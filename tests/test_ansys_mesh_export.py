from dataclasses import replace
from unittest.mock import patch

import numpy as np
import pytest
from pyvista import CellType

from ansys_mesh_export import ansys_export_filters, write_ansys_mesh
from mesh_engine import generate_volume_mesh
from model import Settings


@pytest.fixture(scope="module")
def settings():
    return Settings(size=1, cell_size=1, solid_fraction=36, mesh_resolution=.2)


@pytest.fixture(scope="module")
def hex_mesh(settings):
    return generate_volume_mesh(settings)


@pytest.mark.parametrize(("mesh_type", "cell_types"), [
    ("PRISM6", {CellType.WEDGE}),
    ("PRISM15", {CellType.QUADRATIC_WEDGE}),
    ("PYRAMID5", {CellType.PYRAMID}),
    ("PYRAMID13", {CellType.QUADRATIC_PYRAMID}),
    ("HYBRID8", {CellType.HEXAHEDRON, CellType.PYRAMID}),
    ("HYBRID20", {CellType.QUADRATIC_HEXAHEDRON, CellType.QUADRATIC_PYRAMID}),
])
def test_derived_mesh_conforms_and_preserves_volume(settings, hex_mesh, mesh_type, cell_types):
    mesh = generate_volume_mesh(replace(settings, mesh_type=mesh_type))
    assert set(mesh.grid.celltypes) == cell_types
    linear = mesh.grid.linear_copy()
    volumes = linear.compute_cell_sizes().cell_data["Volume"]
    assert np.all(volumes > 0)
    assert volumes.sum() == pytest.approx(hex_mesh.grid.volume)
    surface = linear.extract_surface(algorithm="dataset_surface")
    assert surface.area == pytest.approx(hex_mesh.grid.extract_surface(algorithm="dataset_surface").area)


def test_formats_follow_mesh_topology_and_installed_tools(settings, hex_mesh, tmp_path):
    quadratic = generate_volume_mesh(replace(settings, mesh_type="HYBRID20"))
    with patch("ansys_mesh_export._native_executable", return_value=None):
        assert [item.rsplit("*", 1)[-1][:-1] for item in ansys_export_filters(quadratic)] == [
            ".cdb", ".bdf", ".dat", ".nas",
        ]
        assert len(ansys_export_filters(hex_mesh)) == 8
    with patch("ansys_mesh_export._native_executable", return_value="ansys.exe"):
        assert len(ansys_export_filters(hex_mesh)) == 11
        assert "Workbench mesh (*.acmo)" in ansys_export_filters(quadratic)
        assert "Fluent case (*.cas)" not in ansys_export_filters(quadratic)

    for extension, header in (("cdb", "/COM,"), ("inp", "*HEADING"),
                              ("bdf", "$"), ("dat", "$"), ("nas", "$"),
                              ("k", "*KEYWORD"), ("key", "*KEYWORD"), ("msh", '(1 "')):
        path = write_ansys_mesh(hex_mesh, tmp_path / f"mesh.{extension}")
        assert path.read_text(encoding="ascii").startswith(header)

    destination = tmp_path / "existing.msh"
    destination.write_text("previous file")
    with pytest.raises(ValueError, match="cannot be exported as"):
        write_ansys_mesh(quadratic, destination)
    assert destination.read_text() == "previous file"
