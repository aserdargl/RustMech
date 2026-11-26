import json
import os

from result_history import discover_results


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
