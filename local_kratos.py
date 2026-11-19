"""Per-user Windows runtime for the RustMech Kratos driver."""

import os
from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
import sys


VERSION = "10.4.4"
INSTALL_REQUIRED = "Install local Kratos from the Kratos settings before running this analysis."
PACKAGES = (
    "KratosMultiphysics",
    "KratosStructuralMechanicsApplication",
    "KratosContactStructuralMechanicsApplication",
    "KratosConstitutiveLawsApplication",
    "KratosLinearSolversApplication",
)
PROBE = "; ".join((
    "from importlib.metadata import version",
    f"assert all(version(package) == {VERSION!r} for package in {PACKAGES!r})",
    "import numpy",
    "import KratosMultiphysics.StructuralMechanicsApplication",
    "import KratosMultiphysics.ContactStructuralMechanicsApplication",
    "import KratosMultiphysics.ConstitutiveLawsApplication",
    "import KratosMultiphysics.LinearSolversApplication",
))


def runtime_directory():
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "RustMech" / "Kratos" / VERSION


def local_kratos_python():
    if os.name == "nt":
        directory = runtime_directory()
        python = directory / "Scripts" / "python.exe"
        if python.is_file() and (directory / "rustmech-ready").is_file():
            return python
    return None


@contextmanager
def system_dll_search():
    """Keep the bundled Qt/Python libraries out of an external Python process."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        yield
        return
    from ctypes import windll

    windll.kernel32.SetDllDirectoryW(None)
    try:
        yield
    finally:
        windll.kernel32.SetDllDirectoryW(sys._MEIPASS)


def _find_python():
    candidates = []
    if not getattr(sys, "frozen", False):
        candidates.append([sys._base_executable])
    launcher = shutil.which("py")
    if launcher:
        candidates.append([launcher, "-3.14"])
    for name in ("python3.14", "python"):
        executable = shutil.which(name)
        if executable:
            candidates.append([executable])
    for command in candidates:
        try:
            probe = subprocess.run(
                [*command, "-I", "-c",
                 "import sys, platform; sys.exit(not (sys.version_info[:2] == (3, 14) "
                 "and sys.implementation.name == 'cpython' and platform.machine().lower() == 'amd64'))"],
                capture_output=True, timeout=10, check=False, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0:
            return command
    raise RuntimeError("Install 64-bit Python 3.14 from python.org, then retry Kratos installation.")


def install_local_kratos():
    """Install binary wheels and publish readiness only after the import check."""
    if os.name != "nt":
        raise RuntimeError("Local Kratos installation is supported on Windows.")
    import msvcrt

    directory = runtime_directory()
    directory.mkdir(parents=True, exist_ok=True)
    python = directory / "Scripts" / "python.exe"
    ready = directory / "rustmech-ready"
    log_path = directory / "install.log"
    with (directory / "install.lock").open("a+b") as lock:
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError("Another RustMech window is installing local Kratos.") from error

        if not ready.is_file() or not python.is_file():
            ready.unlink(missing_ok=True)
            with log_path.open("w", encoding="utf-8") as log, system_dll_search():
                for command in (
                    [*_find_python(), "-I", "-m", "venv", str(directory)],
                    [str(python), "-I", "-m", "pip", "--isolated", "install", "--index-url",
                     "https://pypi.org/simple", "--disable-pip-version-check", "--only-binary=:all:",
                     "--timeout", "30", "--retries", "2", "numpy",
                     *(f"{package}=={VERSION}" for package in PACKAGES)],
                    [str(python), "-I", "-m", "pip", "check"],
                    [str(python), "-I", "-c", PROBE],
                ):
                    result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=900)
                    if result.returncode != 0:
                        raise RuntimeError(f"Kratos installation failed. See {log_path}")
            temporary = ready.with_suffix(".tmp")
            temporary.write_text(VERSION, encoding="utf-8")
            temporary.replace(ready)
    return str(python)


if __name__ == "__main__":
    print(install_local_kratos())
