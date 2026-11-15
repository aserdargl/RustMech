"""Keep the unimplemented solid adapter inactive and free of file side effects."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from model import FEMesh, Settings
from openfoam_backend import OpenFoamBackend, write_openfoam_case_skeleton
from solver_api import (
    AnalysisRequest,
    OPENFOAM_CAPABILITIES,
    create_solver_backend,
    get_solver_capabilities,
)


class OpenFoamBackendTests(unittest.TestCase):
    def test_registered_solid_adapter_is_inactive(self):
        capabilities = get_solver_capabilities("openfoam")
        self.assertIs(capabilities, OPENFOAM_CAPABILITIES)
        self.assertFalse(capabilities.implemented)
        settings = replace(Settings(), solver_backend="openfoam")
        self.assertIn(capabilities.unavailable_reason, capabilities.validate(settings))

    def test_factory_returns_the_solid_adapter(self):
        backend = create_solver_backend("openfoam")
        self.assertIsInstance(backend, OpenFoamBackend)
        self.assertFalse(backend.capabilities.implemented)

    def test_solve_reports_no_attempt_and_creates_no_case(self):
        with TemporaryDirectory() as directory:
            case = Path(directory) / "solid-case"
            request = AnalysisRequest(
                replace(Settings(), solver_backend="openfoam"), Mock(spec=FEMesh), case
            )
            execution = create_solver_backend("openfoam").solve(request)
            self.assertFalse(execution.solve_attempted)
            self.assertIsNone(execution.results)
            self.assertTrue(execution.error)
            self.assertEqual(execution.diagnostics["status"], "placeholder_inactive")
            self.assertFalse(case.exists())

    def test_export_refuses_without_overwriting_existing_files(self):
        with TemporaryDirectory() as directory:
            case = Path(directory)
            sentinel = case / "existing.txt"
            sentinel.write_bytes(b"keep existing case")
            with self.assertRaises(NotImplementedError) as raised:
                write_openfoam_case_skeleton(Mock(spec=FEMesh), Settings(), case)
            self.assertEqual(str(raised.exception), OPENFOAM_CAPABILITIES.unavailable_reason)
            self.assertEqual(sentinel.read_bytes(), b"keep existing case")
            self.assertEqual(list(case.iterdir()), [sentinel])


if __name__ == "__main__":
    unittest.main()
