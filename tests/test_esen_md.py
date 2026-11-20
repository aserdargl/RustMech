"""MD package generation and its handoff to the existing ESEN runner."""

from collections import Counter
from concurrent.futures import Future
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import esen_jobs as jobs
import esen_md as md
from esen_workspace import EsenWorkspace


@pytest.fixture
def potentials(tmp_path):
    library, parameters = tmp_path / "library.meam", tmp_path / "alloy.meam"
    library.write_text(
        "# pair_coeff * * library.meam Nb Hf alloy.meam Hf Nb\n"
        "'Hf' 'hcp' 12 72 178.49 4 2 2 2 2 3.2 7.3 0.8 1 2 1 -6 1 3\n"
        "'Nb' 'bcc' 8 41 92.906 4 5 1 2 1 3.3 7.47 0.72 1 1.7 2.8 -1.6 1 3\n",
        encoding="ascii",
    )
    parameters.write_text("rc = 5\nEc(1,2) = 4.5\n", encoding="ascii")
    return library, parameters


def test_model_matches_e20_and_rounds_close_to_a_bcc_cell():
    model = md.plan_model("Hf, Nb, Ta, Ti, Zr", 250_000, 3.4, 300, 42)
    assert model.repeats == (50, 50, 50)
    assert model.counts == (50_000,) * 5
    smaller = md.plan_model("Hf:25, Nb:75", 17, 3.4, 300, 42)
    assert smaller.repeats == (2, 2, 2)
    assert smaller.counts == (4, 12)
    with pytest.raises(ValueError):
        md.plan_model("Hf:20, Nb:75", 16, 3.4, 300, 42)
    with pytest.raises(ValueError):
        md.plan_model("Hf, Hf", 16, 3.4, 300, 42)


def test_package_distinguishes_meam_indices_from_atom_types(tmp_path, potentials):
    model = md.plan_model("Hf:25, Nb:75", 16, 3.4, 300, 42)
    order = md.meam_order(md.documented_meam_order(*potentials), model.elements)
    assert order == ("Nb", "Hf")

    def fake_atomsk(argv, *, cwd, **_):
        assert argv[1:9] == ["--create", "bcc", "3.4", "Hf", "-duplicate", "2", "2", "2"]
        (cwd / argv[-1]).write_text(
            "Atomsk\n\n16 atoms\n1 atom types\n\n0 6.8 xlo xhi\n0 6.8 ylo yhi\n0 6.8 zlo zhi\n"
            "\nMasses\n\n1 178.49\n\nAtoms # atomic\n\n"
            + "".join(f"{i} 1 {i / 10} 0 0\n" for i in range(1, 17)), encoding="ascii",
        )
        return SimpleNamespace(returncode=0, stdout=b"Atomsk finished")

    with patch("esen_md.subprocess.run", side_effect=fake_atomsk):
        first = md.prepare_job(tmp_path, model, *potentials, order, "atomsk")
        second = md.prepare_job(tmp_path, model, *potentials, order, "atomsk")
    data = (first / "structure.lmp").read_text()
    assert data == (second / "structure.lmp").read_text()
    assert Counter(line.split()[1] for line in data.split("Atoms # atomic")[1].splitlines() if line) == {
        "1": 4, "2": 12,
    }
    analysis = (first / "analiz.inc").read_text()
    assert "meam_kutuphane.meam Nb Hf meam_parametreler.meam Hf Nb" in analysis
    assert "dump_modify atoms sort id element Hf Nb" in analysis
    assert json.loads((first / "model.json").read_text())["counts"] == [4, 12]


def test_bad_meam_indices_stop_before_atomsk(tmp_path, potentials):
    model = md.plan_model("Hf, Nb", 16, 3.4, 300, 42)
    potentials[1].write_text("Ec(1,3) = 4.5\n", encoding="ascii")
    output = tmp_path / "packages"
    with patch("esen_md.subprocess.run") as run_atomsk, pytest.raises(ValueError, match="Ec"):
        md.prepare_job(output, model, *potentials, ("Nb", "Hf"), "atomsk")
    run_atomsk.assert_not_called()
    assert list(output.iterdir()) == []


def test_md_tab_enter_previews_and_run_hands_package_to_esen(tmp_path, potentials):
    class Truba(QObject):
        connection_changed = Signal(bool)
        auth = None

        def analysis_auth(self):
            return self.auth

    app = QApplication.instance() or QApplication([])
    truba = Truba()
    preferences = QSettings(str(tmp_path / "qt.ini"), QSettings.Format.IniFormat)
    with patch("esen_workspace.QSettings", return_value=preferences):
        workspace = EsenWorkspace(truba, history=tmp_path / "jobs")
    try:
        workspace.tabs.setCurrentIndex(1)
        workspace.show()
        app.processEvents()
        workspace.md_atoms.lineEdit().setText("128")
        QTest.keyClick(workspace.md_atoms.lineEdit(), Qt.Key.Key_Return)
        assert workspace.md_atoms.value() == 128
        assert "4 × 4 × 4" in workspace.md_preview.toPlainText()
        assert workspace.md_task is None
        workspace.md_run.click()
        assert "TRUBA" in workspace.md_note.text()
        assert workspace.history.rowCount() == 0

        truba.auth = SimpleNamespace(username="account")
        workspace.md_elements.setText("Hf, Nb")
        workspace.md_order.setText("Nb Hf")
        workspace.md_library.setText(str(potentials[0]))
        workspace.md_parameters.setText(str(potentials[1]))
        package = tmp_path / "model"
        package.mkdir()
        prepared = Future()
        prepared.set_result(package)
        with patch("esen_md.atomsk_executable", return_value="atomsk"), \
                patch.object(workspace.executor, "submit", return_value=prepared) as execute, \
                patch.object(workspace, "_submit_remote") as submit:
            workspace.md_run.click()
            execute.assert_called_once()
            assert workspace.md_task is not None
            workspace.upload_task = object()
            workspace._check_md()
            submit.assert_not_called()
            workspace.upload_task = None
            workspace._check_md()
            directory, record, files, settings, auth = submit.call_args.args
        assert workspace.tabs.currentIndex() == 0
        assert jobs.read_job(directory) == record
        assert record["target"] == "remote"
        assert files == (package, md.ENTRY, (), True)
        assert settings["resume_input"] == md.RESTART
        assert settings["partition"] == "hamsi"
        assert settings["module"] == jobs.MODULES["hamsi"]
        assert auth is truba.auth
    finally:
        workspace.dispose()
