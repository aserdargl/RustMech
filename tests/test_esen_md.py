"""MD package generation and its handoff to the existing ESEN runner."""

from collections import Counter
from concurrent.futures import Future
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

import esen_jobs as jobs
import esen_md as md
from esen_elements import AlloyElements
from esen_workspace import EsenWorkspace
from ui_language import LANGUAGE_KEY, LanguageManager
from window_state import WindowStateStore


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


def test_requested_box_preserves_default_and_rounds_each_axis_to_bcc_cells():
    model = md.plan_model("Hf, Nb, Ta, Ti, Zr", (170, 170, 170), 3.4, 300, 42)
    assert model.repeats == (50, 50, 50)
    assert model.counts == (50_000,) * 5
    smaller = md.plan_model("Hf:25, Nb:75", (7.1, 9.7, 13.5), 3.4, 300, 42)
    assert smaller.repeats == (2, 3, 4)
    assert smaller.box == pytest.approx((6.8, 10.2, 13.6))
    assert smaller.counts == (12, 36)
    with pytest.raises(ValueError, match="3D polycrystal"):
        md.plan_model("Nb", (3.4, 10, 14), 3.4, 300, 42, grains=3)
    with pytest.raises(ValueError, match="exceeds"):
        md.plan_model("Nb", (1_000, 1_000, 1_000), 3.4, 300, 42)
    with pytest.raises(ValueError):
        md.plan_model("Hf:20, Nb:75", (6.8, 6.8, 6.8), 3.4, 300, 42)
    with pytest.raises(ValueError):
        md.plan_model("Hf, Hf", (6.8, 6.8, 6.8), 3.4, 300, 42)


@pytest.mark.parametrize(("cutoff", "skin"), ((5.0, 3.0), (4.0, 3.9)))
def test_package_distinguishes_meam_indices_from_atom_types(tmp_path, potentials, cutoff, skin):
    potentials[1].write_text(f"rc = {cutoff}\nEc(1,2) = 4.5\n", encoding="ascii")
    model = md.plan_model("Hf:25, Nb:75", (6.8, 6.8, 6.8), 3.4, 300, 42)
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
    assert f"neighbor {skin:.1f} bin" in analysis
    assert "dump_modify atoms sort id element Hf Nb" in analysis
    assert json.loads((first / "model.json").read_text())["counts"] == [4, 12]


def test_bad_meam_indices_stop_before_atomsk(tmp_path, potentials):
    model = md.plan_model("Hf, Nb", (6.8, 6.8, 6.8), 3.4, 300, 42)
    potentials[1].write_text("Ec(1,3) = 4.5\n", encoding="ascii")
    output = tmp_path / "packages"
    with patch("esen_md.subprocess.run") as run_atomsk, pytest.raises(ValueError, match="Ec"):
        md.prepare_job(output, model, *potentials, ("Nb", "Hf"), "atomsk")
    run_atomsk.assert_not_called()
    assert list(output.iterdir()) == []


def test_potential_cutoff_shorter_than_cna_stops_before_atomsk(tmp_path, potentials):
    model = md.plan_model("Hf, Nb", (6.8, 6.8, 6.8), 3.4, 300, 42)
    potentials[1].write_text("rc = 3.8 # shorter than E20 CNA\n", encoding="ascii")
    with patch("esen_md.subprocess.run") as run_atomsk, pytest.raises(ValueError, match=r"rc = 3\.8.*3\.9"):
        md.prepare_job(tmp_path, model, *potentials, ("Nb", "Hf"), "atomsk")
    run_atomsk.assert_not_called()
    assert list(tmp_path.glob("md-bcc-*")) == []


def test_polycrystal_package_uses_actual_atom_count_and_seeded_nodes(tmp_path, potentials):
    model = md.plan_model("Hf:25, Nb:75", (6.8, 10.2, 13.6), 3.4, 300, 42, grains=3)
    order = md.meam_order("Nb Hf", model.elements)
    node_files = []

    def fake_atomsk(argv, *, cwd, **_):
        if argv[1] == "--create":
            assert argv[2:] == ["bcc", "3.4", "Hf", "seed.xsf"]
            (cwd / "seed.xsf").touch()
        else:
            assert argv[1:3] == ["--polycrystal", "seed.xsf"]
            assert argv[5:7] == ["-wrap", "-remove-doubles"]
            node_files.append((cwd / argv[3]).read_text())
            (cwd / argv[4]).write_text(
                "Atomsk\n\n16 atoms\n1 atom types\n\nMasses\n\n1 178.49\n\nAtoms # atomic\n\n"
                + "".join(f"{i} 1 {i / 10} 0 0\n" for i in range(1, 17)), encoding="ascii",
            )
        return SimpleNamespace(returncode=0, stdout=b"")

    with patch("esen_md.subprocess.run", side_effect=fake_atomsk):
        first = md.prepare_job(tmp_path, model, *potentials, order, "atomsk")
        second = md.prepare_job(tmp_path, model, *potentials, order, "atomsk")

    assert node_files[0] == node_files[1] == (first / "grain-nodes.txt").read_text()
    box, *nodes = node_files[0].splitlines()
    assert list(map(float, box.split()[1:])) == pytest.approx(model.box)
    assert len(nodes) == 3 and all(line.startswith("node ") for line in nodes)
    metadata = json.loads((second / "model.json").read_text())
    assert metadata["estimated_atoms"] == 48
    assert metadata["requested_box_angstrom"] == metadata["box_angstrom"] == [6.8, 10.2, 13.6]
    assert (metadata["grains"], metadata["atoms"], metadata["counts"]) == (3, 16, [4, 12])
    assert Counter(line.split()[1] for line in (first / "structure.lmp").read_text().split(
        "Atoms # atomic")[1].splitlines() if line) == {"1": 4, "2": 12}


def test_md_plan_library_reopens_generated_and_added_packages(tmp_path):
    class Truba(QObject):
        connection_changed = Signal(bool)

        def analysis_auth(self):
            return None

    def package(folder):
        folder.mkdir(parents=True)
        for name in md.REQUIRED_PACKAGE_FILES:
            (folder / name).touch()
        (folder / md.ENTRY).write_text(md.INITIAL, encoding="utf-8")
        (folder / md.PLAN_METADATA).write_text(json.dumps({
            "elements": ["Nb"], "counts": [128], "atoms": 128, "grains": 1,
            "temperature": 300, "target_atoms": 128, "meam_order": ["Nb"],
        }), encoding="utf-8")
        return folder

    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "qt.ini"), QSettings.Format.IniFormat)
    store = WindowStateStore(app, settings)
    generated = package(tmp_path / "md-preparations" / "md-bcc-300K-sample")
    added = package(tmp_path / "shared-plan")
    truba = Truba()
    windows = []
    try:
        with patch("esen_workspace.QSettings", return_value=settings):
            first = EsenWorkspace(truba, history=tmp_path / "jobs")
            windows.append(first)
            first.tabs.setCurrentIndex(1)
            first.show()
            app.processEvents()
            assert first.md_plans.count() == 1
            assert first.md_plans.currentItem().data(Qt.ItemDataRole.UserRole) == generated
            assert "Nb  128" in first.md_plan_source.toPlainText()
            with patch("esen_workspace.QFileDialog.getExistingDirectory", return_value=str(tmp_path)):
                first.md_add_plan.click()
            assert "Choose a complete" in first.md_plan_hint.text()
            assert first.md_plans.count() == 1
            with patch("esen_workspace.QFileDialog.getExistingDirectory", return_value=str(added)):
                first.md_add_plan.click()
                first.md_add_plan.click()
            assert first.md_plans.count() == 2
            assert first.md_plans.currentItem().data(Qt.ItemDataRole.UserRole) == added
            first.md_plan_file.setCurrentIndex(first.md_plan_file.findData(md.ENTRY))
            assert first.md_plan_source.toPlainText() == md.INITIAL
            split = next(s for s in store.splitters(first) if s.objectName() == "esenMdPreviewAndPlans")
            split.setSizes([280, 420])
            expected = split.sizes()
            first.close()

            second = EsenWorkspace(truba, history=tmp_path / "jobs")
            windows.append(second)
            second.tabs.setCurrentIndex(1)
            second.show()
            app.processEvents()
            assert second.md_plans.count() == 2
            restored = next(s for s in store.splitters(second) if s.objectName() == "esenMdPreviewAndPlans")
            assert restored.sizes() == expected
    finally:
        for window in reversed(windows):
            window.dispose()
        app.removeEventFilter(store)


def test_alloy_elements_fill_left_column_then_continue_on_right():
    app = QApplication.instance() or QApplication([])
    first = ("Hf", "Nb", "Ta", "Ti", "Zr")
    additional = ("Fe", "Mo", "W", "V", "Cr", "Ni")
    elements = AlloyElements((symbol, 20) for symbol in first)
    elements.show()
    try:
        for symbol in additional:
            elements.cells[symbol][1].trigger()
        app.processEvents()

        def row_at(row, column):
            return elements.list_layout.itemAtPosition(row, column).widget()

        for index, symbol in enumerate(first):
            assert row_at(index, 0) is elements.rows[symbol][0]
        for index, symbol in enumerate(additional):
            assert row_at(index, 1) is elements.rows[symbol][0]
        assert row_at(0, 0).y() == row_at(0, 1).y()
        assert row_at(0, 0).geometry().right() < row_at(0, 1).x()

        elements.rows["Fe"][1].setValue(20)
        elements.remove_element("Nb")
        assert row_at(4, 0) is elements.rows["Fe"][0]
        assert row_at(0, 1) is elements.rows["Mo"][0]
        assert elements.composition().startswith("Hf:20.0000, Ta:20.0000, Ti:20.0000, Zr:20.0000, Fe:20.0000")
        assert elements.total_label.text().endswith("100 %")
    finally:
        elements.close()


def test_md_tab_enter_previews_and_submits_package_to_truba(tmp_path, potentials):
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
        workspace.show()
        app.processEvents()
        assert workspace.windowTitle() == "Molecular Dynamics"
        assert workspace.tabs.count() == 1
        assert workspace.tabs.tabText(0) == "MD Preparation"
        assert not workspace.md_grains.isVisible()
        workspace.md_dimensions[1].setValue(1.02)
        workspace.md_dimensions[2].setValue(1.36)
        length = workspace.md_dimensions[0]
        length.lineEdit().setText("0.7")
        QTest.keyClick(length.lineEdit(), Qt.Key.Key_Return)
        assert length.value() == pytest.approx(0.7)
        assert preferences.value("esen/md/box/x", type=float) == pytest.approx(0.7)
        preview = workspace.md_preview.toPlainText()
        assert "2 × 3 × 4" in preview
        assert "0.68 × 1.02 × 1.36 nm" in preview
        assert "Generated atom count   48" in preview
        assert "rounded" in preview

        for source_button in workspace.md_file_buttons[1::2]:
            with patch("esen_workspace.QDesktopServices.openUrl") as open_url:
                source_button.menu().actions()[1].trigger()
                open_url.assert_called_once()
                assert open_url.call_args.args[0].host() == "www.ctcms.nist.gov"
        length.setValue(1.36)
        assert workspace.md_task is None
        workspace.md_crystal.setCurrentIndex(workspace.md_crystal.findData("poly"))
        assert workspace.md_grains.isVisible()
        assert "Characteristic grain size" in workspace.md_preview.toPlainText()
        workspace.md_xrd_spacing.setValue(2.4)
        assert workspace.md_xrd_result.text() == "a = 3.3941 Å"
        workspace.md_apply_lattice.click()
        assert workspace.md_lattice.value() == pytest.approx(3.3941)
        assert workspace.md_task is None
        workspace.md_run.click()
        assert "TRUBA" in workspace.md_note.text()
        assert not list(workspace.history_root.glob("*/job.json"))

        elements = workspace.md_elements
        assert len(elements.cells) == 118
        assert elements.total_label.text().endswith("100 %")
        for symbol in ("Ta", "Ti", "Zr"):
            elements.remove_element(symbol)
        assert elements.total_label.text().endswith("40 %")

        cell, add_action = elements.cells["Fe"]
        menus = []

        def add_from_context_menu():
            menu = app.activePopupWidget()
            if menu is not None:
                menus.extend(action.text() for action in menu.actions())
                QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(add_action).center())
                menu.close()

        QTimer.singleShot(0, add_from_context_menu)
        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, cell.rect().center(),
                                  cell.mapToGlobal(cell.rect().center()))
        app.sendEvent(cell, event)
        assert menus == ["Add"]
        assert "Fe" in elements.rows
        assert not add_action.isEnabled()
        elements.remove_element("Fe")
        assert add_action.isEnabled()

        for symbol, total in (("Hf", 70), ("Nb", 100)):
            percent = elements.rows[symbol][1]
            edit = percent.lineEdit()
            edit.setFocus()
            edit.selectAll()
            QTest.keyClicks(edit, "50")
            assert elements.total_label.text().endswith(f"{total} %")
            QTest.keyClick(edit, Qt.Key.Key_Return)
        assert elements.total_label.text().endswith("100 %")
        assert "Hf       " in workspace.md_preview.toPlainText()
        assert workspace.md_task is None

        preferences.setValue(LANGUAGE_KEY, "tr")
        language = LanguageManager(app, preferences)
        try:
            with patch.object(app, "_rustmech_language", language, create=True):
                elements.add_element("No")
                cell = elements.cells["No"][0]
                label = elements.rows["No"][0].findChild(QLabel)
                language.localize(cell)
                language.localize(label)
                assert cell.text() == cell.accessibleName() == label.text() == "No"
                elements.remove_element("No")
        finally:
            language.shutdown()
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
            workspace.md_prepare.click()
            execute.assert_called_once()
            assert execute.call_args.args[2].grains == 12
            workspace._check_md()
            submit.assert_not_called()
            assert str(package) in workspace.md_note.text()
            truba.auth = SimpleNamespace(username="account")
            workspace.md_grains.setValue(13)
            workspace.md_run.click()
            runs = [call for call in execute.call_args_list if call.args[0] is md.prepare_job]
            assert [call.args[2].grains for call in runs] == [12, 13]
            workspace.remote_task = object()
            workspace._check_md()
            submit.assert_not_called()
            workspace.remote_task = None
            workspace._check_md()
            directory, record, files, settings, auth = submit.call_args.args
        assert workspace.tabs.currentIndex() == 0
        assert jobs.read_job(directory) == record
        assert record["target"] == "remote"
        assert files == (package, md.ENTRY, (), True)
        assert settings["resume_input"] == md.RESTART
        assert settings["partition"] == "barbun"
        assert settings["module"] == jobs.DEFAULT_MODULE
        assert auth is truba.auth
    finally:
        workspace.dispose()
