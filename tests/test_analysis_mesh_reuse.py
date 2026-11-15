"""The supplied solid mesh reaches MAPDL unchanged, without a meshing call."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import pyvista as pv

import mapdl_backend as adapter
import mesh_engine
from model import Settings
from solver_api import AnalysisRequest, MAPDL_CAPABILITIES, SolverExecution
from tetrahedral_mesh import generate_tetrahedral_mesh


class AnalysisMeshReuseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.meshes = {}
        for kind in ("HEX8", "HEX20", "TET4", "TET10"):
            settings = Settings(size=1, cell_size=1, compression=0.1,
                                mesh_type=kind,
                                mesh_resolution=0.25 if kind.startswith("TET") else 0.1,
                                local_mesh_size=0.2)
            if kind.startswith("TET"):
                mesh = generate_tetrahedral_mesh(
                    settings, pv.Box(bounds=(0, 1, 0, 1, 0, 1)).triangulate()
                )
            else:
                mesh = mesh_engine.generate_volume_mesh(settings)
            cls.meshes[kind] = settings, mesh

    def imported_mesh(self, mesh):
        grid = mesh.grid
        element_type, cells = adapter._solid_connectivity(grid)
        records = np.zeros((grid.n_cells, 10 + cells.shape[1]), dtype=np.int64)
        records[:, 8] = grid.cell_data["ansys_elem_num"]
        records[:, 10:] = grid.point_data["ansys_node_num"][cells]
        # MAPDL is allowed to return records in a different order.
        return SimpleNamespace(
            nnum=grid.point_data["ansys_node_num"][::-1].copy(),
            nodes=grid.points[::-1].copy(),
            enum=grid.cell_data["ansys_elem_num"][::-1].copy(),
            etype=np.full(grid.n_cells, element_type), elem=records[::-1].copy(),
        )

    def test_all_supported_meshes_are_imported_and_verified_without_remeshing(self):
        for kind, (settings, mesh) in self.meshes.items():
            with self.subTest(kind=kind), TemporaryDirectory() as folder:
                mapdl = MagicMock()
                mapdl.mesh = self.imported_mesh(mesh)
                backend = adapter.MapdlBackend(MagicMock(return_value=mapdl), MagicMock())
                with patch.object(adapter, "prepare_ansys", return_value=(100, 101)) as prepare:
                    result = backend.solve(AnalysisRequest(settings, mesh, Path(folder)))
                self.assertTrue(result.solve_attempted, result.error)
                self.assertTrue(result.diagnostics["mesh_import_verified"])
                self.assertFalse(result.diagnostics["specimen_remeshed"])
                self.assertIs(prepare.call_args.args[2], mesh)
                mapdl.input.assert_called_once_with(str(Path(folder) / "gyroid_mesh.inp"))
                for command in ("vmesh", "amesh", "lmesh", "remesh", "nummrg"):
                    getattr(mapdl, command).assert_not_called()
                exported = (Path(folder) / "gyroid_mesh.inp").read_text()
                self.assertNotIn("VMESH", exported.upper())

    def test_changed_coordinates_or_connectivity_stop_before_solve(self):
        for kind, (settings, mesh) in self.meshes.items():
            for changed in ("coordinates", "connectivity"):
                with self.subTest(kind=kind, changed=changed), TemporaryDirectory() as folder:
                    mapdl = MagicMock()
                    mapdl.mesh = self.imported_mesh(mesh)
                    if changed == "coordinates":
                        mapdl.mesh.nodes[0, 0] += 0.01
                    else:
                        mapdl.mesh.elem[0, 10] = mapdl.mesh.elem[0, 11]
                    backend = adapter.MapdlBackend(MagicMock(return_value=mapdl), MagicMock())
                    result = backend.solve(AnalysisRequest(settings, mesh, Path(folder)))
                    self.assertIn("MAPDL import changed FE", result.error)
                    self.assertFalse(result.solve_attempted)
                    self.assertFalse(result.diagnostics["mesh_import_verified"])
                    mapdl.solve.assert_not_called()

    def test_analysis_forwards_the_existing_mesh_without_generating_another(self):
        import nonlinear_compression as studio

        settings, mesh = self.meshes["HEX8"]
        backend = MagicMock(capabilities=MAPDL_CAPABILITIES)
        backend.solve.return_value = SolverExecution(error="stop after handoff")
        with TemporaryDirectory() as folder:
            paths = SimpleNamespace(human=Path(folder), raw=Path(folder))
            with patch.object(studio.RunPaths, "create", return_value=paths), patch.object(
                studio, "export_geometry", return_value={}
            ), patch.object(studio, "generate_volume_mesh", side_effect=AssertionError("remeshed")), patch.object(
                mesh_engine, "generate_volume_mesh", side_effect=AssertionError("remeshed")
            ):
                with self.assertRaisesRegex(RuntimeError, "stop after handoff"):
                    studio.run_analysis(settings, mesh, backend, images=False)
        request = backend.solve.call_args.args[0]
        self.assertIs(request.mesh, mesh)


if __name__ == "__main__":
    unittest.main()
