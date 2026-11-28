import json
import os

from PySide6.QtWidgets import QApplication

from result_history import ResultHistoryDialog, SavedResult, discover_results


def test_discovery_reads_saved_formats_and_sorts_by_modified_date(tmp_path):
    def write(relative, data, modified):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        os.utime(path, (modified, modified))
        return path

    local = write("HumanResults/Run_1/summary.json", {"status": "completed", "solver": "kratos"}, 100)
    write("results/Run_1/history.csv", [], 999)
    write("HumanResults/unfinished/summary.json", {"status": "solving"}, 999)
    write("results/TRUBA_probe/output/result.json", {"operation": "probe"}, 999)
    write("results/TRUBA_fft/output/result.json", {"operation": "fft_solve", "run_folder": "/old/fft"}, 400)
    fft = write("results/TRUBA_fft/output/fft/status.json", {"state": "complete"}, 300)
    write("results/TRUBA_fft/output/fft/stiffness_mpa.csv", [], 300)
    write("DoEResults/DoE_group/matrix.csv", [], 999)
    doe = write("DoEResults/DoE_group/truba/output/row_00001/experiment.json", {"state": "Completed"}, 200)
    write("DoEResults/DoE_group/truba/output/row_00002/experiment.json", {"state": "Failed"}, 999)

    results = discover_results(tmp_path, tmp_path / "DoEResults")

    assert [(item.path, item.kind) for item in results] == [
        (fft.parent, "DAMASK FFT"),
        (doe.parent, "DoE"),
        (local.parent, "Mechanic"),
    ]
    assert results[2].solver == "kratos"


def test_history_finds_named_local_and_truba_folders(tmp_path):
    project = tmp_path / "project"
    local = tmp_path / "Local compression"
    remote = tmp_path / "TRUBA compression"
    remote_job = remote / "TRUBA"
    remote_output = remote_job / "output" / "HumanResults" / "Run_1"
    local.mkdir()
    remote_output.mkdir(parents=True)
    for folder in (local, remote_output):
        (folder / "summary.json").write_text('{"status": "completed"}', encoding="utf-8")
    (remote_job / "output" / "result.json").write_text('{"operation": "mechanical"}', encoding="utf-8")

    results = discover_results(project, project / "DoEResults", remote_folders=(remote_job,),
                               analysis_folders=(local, remote, tmp_path / "Missing"))

    assert {item.path: item.name for item in results} == {local: local.name, remote_output: remote.name}


def test_mechanical_result_tabs_use_selected_saved_run(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    graphs = tmp_path / "04_Graphs"
    graphs.mkdir()
    (graphs / "force_displacement.csv").write_text(
        "Time,Actuator_travel_mm,Force_N,Gauge_extension_mm\n0,0,0,0\n1,0.4,20,0.2\n", encoding="utf-8")
    (graphs / "stress_strain.csv").write_text(
        "Engineering_strain,Nominal_tensile_stress_MPa\n0,0\n0.01,5\n", encoding="utf-8")
    mechanical = SavedResult(tmp_path, "Tension test", "Mechanic", 20)
    other = SavedResult(tmp_path / "flow", "Flow", "Flow (CFD)", 10)
    opened = []
    monkeypatch.setattr("mechanical_result_tabs.QDesktopServices.openUrl",
                        lambda url: opened.append(url.toLocalFile()) or True)
    dialog = ResultHistoryDialog([mechanical, other], lambda result: opened.append(result) or True, None)
    try:
        assert not dialog.tabs.isTabEnabled(1)
        dialog.table.selectRow(0)
        assert dialog.tabs.isTabEnabled(1)
        dialog.tabs.setCurrentWidget(dialog.datasheet)
        dialog.datasheet.sheet.setCurrentIndex(1)
        model = dialog.datasheet.table.model()
        assert model.index(1, 2).data() == "20"
        dialog.datasheet.excel.click()
        assert opened == [str(tmp_path / "Sonuclar.xlsx").replace("\\", "/")]
        assert (tmp_path / "Sonuclar.xlsx").is_file()

        dialog.tabs.setCurrentWidget(dialog.carbonplot)
        assert list(dialog.carbonplot.axis.lines[0].get_xdata()) == [0.0, 0.2]
        dialog.carbonplot.series.setCurrentIndex(1)
        assert list(dialog.carbonplot.axis.lines[0].get_ydata()) == [0.0, 5.0]
        dialog.tabs.setCurrentIndex(0)
        dialog.table.selectRow(1)
        assert not dialog.tabs.isTabEnabled(1)
        assert not dialog.tabs.isTabEnabled(2)
        dialog.open_button.click()
        assert opened[-1] == other
    finally:
        dialog.close()
        app.processEvents()
