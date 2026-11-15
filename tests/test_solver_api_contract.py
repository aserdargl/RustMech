"""Solver kayit defteri sozlesmesi: bilinen/bilinmeyen kimlikler."""
import unittest

from solver_api import (
    KRATOS_CAPABILITIES, MAPDL_CAPABILITIES, OPENFOAM_CAPABILITIES,
    SOLVER_REGISTRY, create_solver_backend, get_solver_capabilities,
)


class SolverRegistryContractTests(unittest.TestCase):
    def test_registry_contains_exactly_the_known_solvers(self):
        self.assertEqual(set(SOLVER_REGISTRY), {"ansys_mapdl", "openfoam", "kratos"})
        for identifier, caps in SOLVER_REGISTRY.items():
            self.assertEqual(caps.identifier, identifier)

    def test_known_identifiers_resolve_to_their_own_capabilities(self):
        self.assertIs(get_solver_capabilities("ansys_mapdl"), MAPDL_CAPABILITIES)
        self.assertIs(get_solver_capabilities("openfoam"), OPENFOAM_CAPABILITIES)
        self.assertIs(get_solver_capabilities("kratos"), KRATOS_CAPABILITIES)

    def test_unknown_identifier_falls_back_to_mapdl_capabilities(self):
        # Eski/bozuk ayar dosyalari icin mevcut uyumluluk davranisi.
        for identifier in ("", "calculix", "ANSYS_MAPDL"):
            self.assertIs(get_solver_capabilities(identifier), MAPDL_CAPABILITIES)

    def test_creating_an_unknown_backend_fails_instead_of_falling_back(self):
        # Kapasite sorgusunun aksine, calistirma yolu sessizce MAPDL'e dusmez.
        for identifier in ("", "calculix", "ANSYS_MAPDL"):
            with self.assertRaisesRegex(ValueError, "Unsupported solver backend"):
                create_solver_backend(identifier)

    def test_openfoam_slot_is_inactive_and_kratos_is_implemented(self):
        self.assertFalse(OPENFOAM_CAPABILITIES.implemented)
        self.assertTrue(KRATOS_CAPABILITIES.implemented)
        self.assertIn("HEX8", KRATOS_CAPABILITIES.supported_mesh_types)


if __name__ == "__main__":
    unittest.main()
