"""Build an isolated Windows payload and optionally create a GitHub release."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import NamedTemporaryFile, TemporaryDirectory, gettempdir


RELEASE_REPO = "aserdargl/RustMech-Releases"
VERSION_FILE = Path("packaging/version_info.txt")
INSTALLER_SCRIPT = Path("packaging/RustMech.iss")
VERSION_PATTERN = re.compile(r"[^\W_][\w .+-]{0,63}", re.UNICODE)
NUMBER_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+){0,3}")
REPO_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+")


def version_parts(label):
    match = NUMBER_PATTERN.search(label)
    if match is None:
        return label, 1, 0, ""
    parts = match.group().split(".")
    major = int(parts[0])
    minor = int(parts[1]) if len(parts) > 1 else 0
    suffix = "." + ".".join(parts[2:]) if len(parts) > 2 else ""
    return label[:match.start()], major, minor, suffix + label[match.end():]


def release_version(value):
    label = value.strip()
    if not VERSION_PATTERN.fullmatch(label) or label.endswith("."):
        raise ValueError("Sürüm 1–64 karakter olmalı. Harf, sayı, boşluk, nokta, +, - ve _ kullanabilirsiniz.")
    match = NUMBER_PATTERN.search(label)
    numbers = [int(part) for part in match.group().split(".")] if match else []
    if any(number > 65535 for number in numbers):
        raise ValueError("Sürümün sayısal bölümleri 65535'i geçemez.")
    numeric = tuple(numbers + [0] * (4 - len(numbers)))
    tag = re.sub(r"\.+", ".", re.sub(r" +", "-", label))
    if tag.lower().endswith(".lock"):
        tag += "-release"
    return label, numeric, tag


def read_version(root):
    source = (root / VERSION_FILE).read_text(encoding="utf-8-sig")
    match = re.search(r"StringStruct\('ProductVersion', '([^']+)'\)", source)
    if not match:
        raise ValueError("RustMech sürümü okunamadı.")
    return match[1]


def build_python(root, preferred=None):
    candidates = (
        root / ".venv/Scripts/python.exe",
        preferred,
        root.parent / "Gitterburg/.venv/Scripts/python.exe",
        shutil.which("python.exe"),
    )
    return next((Path(candidate) for candidate in candidates if candidate and Path(candidate).is_file()), None)


def inno_compiler():
    locations = ("LOCALAPPDATA", "ProgramFiles(x86)", "ProgramFiles")
    candidates = [shutil.which("ISCC.exe")]
    for location in locations:
        if base := os.environ.get(location):
            directory = Path(base) / "Programs" if location == "LOCALAPPDATA" else Path(base)
            candidates.extend(directory / f"Inno Setup {major}/ISCC.exe" for major in (7, 6))
    return next((Path(candidate) for candidate in candidates if candidate and Path(candidate).is_file()), None)


def check_project(root, python):
    if not all((root / path).is_file() for path in ("Build-Exe.ps1", INSTALLER_SCRIPT, VERSION_FILE)):
        raise ValueError("RustMech kaynak klasörünü seçin.")
    compiler = inno_compiler()
    if compiler is None:
        raise ValueError("Inno Setup 6 veya 7 gerekli. ISCC.exe bulunamadı.")
    if python is None:
        raise ValueError("Derleme için Python bulunamadı.")
    if not shutil.which("powershell.exe") or not shutil.which("cargo.exe"):
        raise ValueError("Derleme için Windows PowerShell ve Rust Cargo gerekli.")
    return compiler


def windows_version_file(root, target, label, numeric):
    source = (root / VERSION_FILE).read_text(encoding="utf-8-sig")
    source = re.sub(r"((?:filevers|prodvers)=)\([^)]+\)", lambda m: m[1] + str(numeric), source)
    source = re.sub(r"('(?:FileVersion|ProductVersion)', )'[^']+'", lambda m: m[1] + repr(label), source)
    target.write_text(source, encoding="utf-8")


def progress(percent, message):
    print(f"PROGRESS|{percent}|{message}", flush=True)


def run(command, root, *, env=None):
    completed = subprocess.run(
        command, cwd=root, stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=sys.stderr,
        creationflags=subprocess.CREATE_NO_WINDOW, env=env, check=False,
    )
    if completed.returncode:
        raise ValueError(f"{Path(command[0]).name} başarısız oldu (çıkış kodu {completed.returncode}).")


def setup_output_directory():
    return Path.home() / "MyDrive/X-MySoftware/RustMech"


@contextmanager
def build_lock(root):
    identity = hashlib.sha256(os.path.normcase(str(root)).encode()).hexdigest()
    lock_path = Path(gettempdir()) / f"rustmech-setup-{identity}.lock"
    try:
        fd = os.open(lock_path, os.O_BINARY | os.O_RDWR | os.O_CREAT | os.O_TEMPORARY)
    except PermissionError as error:
        raise ValueError("Bu proje için bir kurulum derlemesi zaten çalışıyor.") from error
    with os.fdopen(fd, "r+b") as lock:
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise ValueError("Bu proje için bir kurulum derlemesi zaten çalışıyor.") from error
        yield


def copy_installer(setup, output):
    output.mkdir(parents=True, exist_ok=True)
    target = output / setup.name
    with NamedTemporaryFile(dir=output, prefix=".setup-", suffix=".tmp", delete=False) as stream:
        staged = Path(stream.name)
    try:
        shutil.copyfile(setup, staged)
        staged.replace(target)
    finally:
        staged.unlink(missing_ok=True)
    return target


def create_release(root, job, compiler, output):
    label, numeric, tag = release_version(job["version"])
    asset_name = f"RustMech-{tag}-Setup"
    github = shutil.which("gh.exe") if job.get("publish") else None
    repo = job.get("repo", "")
    if job.get("publish"):
        if not github or not REPO_PATTERN.fullmatch(repo):
            raise ValueError("Yayın için GitHub CLI ve kullanıcı/depo biçiminde hedef gerekli.")
        run([github, "repo", "view", repo, "--json", "nameWithOwner"], root)

    with TemporaryDirectory(prefix="rustmech-setup-") as temporary:
        stage = Path(temporary)
        version_file = stage / "version_info.txt"
        windows_version_file(root, version_file, label, numeric)
        progress(10, "RustMech ve bağımlılıkları derleniyor…")
        run([
            "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(root / "Build-Exe.ps1"),
            "-Python", sys.executable, "-DistPath", str(stage / "dist"), "-WorkPath", str(stage / "work"),
            "-SpecPath", str(stage), "-VersionFile", str(version_file), "-CleanRelease",
        ], root, env={key: value for key, value in os.environ.items() if key != "PSMODULEPATH"})

        progress(65, "Windows kurulum dosyası sıkıştırılıyor…")
        compiled = stage / "release"
        run([
            str(compiler), f"/DAppVersion={label}", f"/DNumericVersion={'.'.join(map(str, numeric))}",
            f"/DPayloadDir={stage / 'dist/RustMech'}", f"/DSetupName={asset_name}", f"/O{compiled}",
            str(root / INSTALLER_SCRIPT),
        ], root)
        setup = compiled / f"{asset_name}.exe"
        installer = copy_installer(setup, output)
        print(f"RESULT|{installer}", flush=True)

        if github:
            progress(90, "Kurulum dosyası GitHub'a yükleniyor…")
            notes = stage / "notes.txt"
            notes.write_text(job.get("notes", ""), encoding="utf-8")
            run([github, "release", "create", tag, str(setup), "--repo", repo,
                 "--title", f"RustMech {label}", "--notes-file", str(notes)], root)
    progress(100, "Kurulum dosyası hazır." if not github else "Kurulum dosyası hazır ve GitHub'da yayımlandı.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--check", action="store_true")
    options = parser.parse_args()
    root = options.root.resolve()
    try:
        compiler = check_project(root, Path(sys.executable))
        if options.check:
            run([sys.executable, "-c",
                 "import PyInstaller, PySide6, netgen, paramiko; "
                 "from ansys.mapdl.core import launch_mapdl; from ansys.mapdl.reader import read_binary"], root)
            progress(100, f"Proje ve derleme araçları hazır · {read_version(root)}")
        else:
            job = json.load(sys.stdin)
            with build_lock(root):
                create_release(root, job, compiler, setup_output_directory())
    except (OSError, ValueError, KeyError) as error:
        print(f"ERROR|{error}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
