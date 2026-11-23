"""Check SetupMaker delivery using a fake compiler and a disposable Drive folder."""

from pathlib import Path
from threading import Event, Thread
from unittest.mock import Mock

import pytest

from tools.setupmaker import worker


@pytest.mark.parametrize("publish", [False, True])
def test_build_leaves_only_the_installer_in_drive(tmp_path, monkeypatch, capsys, publish):
    root = tmp_path / "project"
    root.mkdir()
    drive = tmp_path / "drive"
    staging = []

    def fake_run(command, cwd, **_options):
        assert cwd == root
        if command[0] == "ISCC.exe":
            output = next(Path(arg[2:]) for arg in command if arg.startswith("/O"))
            output.mkdir()
            (output / "RustMech-1.2-Setup.exe").write_bytes(b"test setup")
            staging.append(output.parent)
        elif command[1:3] == ["release", "create"]:
            assert Path(command[4]).read_bytes() == b"test setup"
            assert Path(command[-1]).read_text(encoding="utf-8") == "Release notes"

    run = Mock(side_effect=fake_run)
    monkeypatch.setattr(worker, "run", run)
    monkeypatch.setattr(worker, "windows_version_file", Mock())
    monkeypatch.setattr(worker.shutil, "which", lambda _: "gh.exe")
    with worker.build_lock(root):
        worker.create_release(
            root, {"version": "1.2", "publish": publish, "repo": "owner/releases", "notes": "Release notes"},
            Path("ISCC.exe"), drive,
        )

    installer, = drive.iterdir()
    assert installer.read_bytes() == b"test setup"
    assert f"RESULT|{installer}" in capsys.readouterr().out
    assert not staging[0].exists()
    assert not list(root.iterdir())
    assert any(call.args[0][1:3] == ["release", "create"] for call in run.call_args_list) is publish


def test_stop_kills_compiler_descendants_before_cleaning_stage(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    drive = tmp_path / "drive"
    decision = tmp_path / "decision"
    ready = tmp_path / "ready"
    helper = Path(worker.__file__).parent
    child = tmp_path / "compile.py"
    child.write_text(
        """import subprocess, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from build_process import join_build_job
join_build_job(sys.argv[-1])
subprocess.Popen([sys.executable, "-c",
    "import sys, time; from pathlib import Path; "
    "held = open(Path(sys.argv[1]) / 'held-open', 'w'); "
    "Path(sys.argv[2]).touch(); time.sleep(30)",
    sys.argv[2], sys.argv[3]])
time.sleep(30)
""",
        encoding="utf-8",
    )
    staging = []
    real_run = worker.run_build_tree

    def compile_fixture(arguments, request):
        stage = Path(arguments[arguments.index("--compile-stage") + 1])
        staging.append(stage)
        real_run([str(child), str(helper), str(stage), str(ready)], request)

    monkeypatch.setattr(worker, "run_build_tree", compile_fixture)
    finished = Event()

    def request_stop():
        while not finished.wait(0.05):
            if ready.exists():
                decision.touch(exist_ok=False)
                return

    controller = Thread(target=request_stop)
    controller.start()
    try:
        with pytest.raises(worker.BuildStopped):
            worker.create_release(root, {"version": "1.2", "decision": str(decision)}, Path("ISCC.exe"), drive)
    finally:
        finished.set()
        controller.join()
    assert ready.exists()
    assert not staging[0].exists()
    assert not drive.exists()
