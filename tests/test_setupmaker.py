"""Check SetupMaker delivery using a fake compiler and a disposable Drive folder."""

from pathlib import Path
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
