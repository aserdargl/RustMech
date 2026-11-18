"""Build the standalone Setup Maker executable into dist/SetupMaker.exe."""

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


def main():
    root = Path(__file__).resolve().parents[2]
    output = root / "dist"
    output.mkdir(exist_ok=True)
    environment = os.environ.copy()
    system_directory = Path(os.environ["SystemRoot"]) / "System32"
    # Qt uses Windows ICU. A Poppler installation can expose a DLL with the
    # same name and incompatible exports to PyInstaller's dependency scanner.
    environment["PATH"] = os.pathsep.join(
        entry for entry in os.get_exec_path(environment)
        if entry and (
            Path(entry).resolve() == system_directory or not (Path(entry) / "icuuc.dll").is_file()
        )
    )
    with TemporaryDirectory(prefix="setupmaker-", dir=output) as temporary:
        stage = Path(temporary)
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed",
            "--name", "SetupMaker", "--paths", str(root), "--distpath", str(stage),
            "--workpath", str(stage / "work"), "--specpath", str(stage),
            "--icon", str(root / "assets/ui/rustmech.ico"),
            "--add-data", f"{root / 'assets/ui'};assets/ui",
            "--add-data", f"{root / 'tools/setupmaker/worker.py'};tools/setupmaker",
            str(root / "tools/setupmaker/main.py"),
        ], cwd=root, env=environment, check=True)
        target = output / "SetupMaker.exe"
        try:
            (stage / target.name).replace(target)
        except PermissionError as error:
            raise SystemExit("Setup Maker'ı kapatıp derlemeyi yeniden çalıştırın.") from error
    print(target)


if __name__ == "__main__":
    main()
