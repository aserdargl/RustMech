"""SFTP file operations within the authenticated user's RustMech project."""

from contextlib import contextmanager, suppress
from pathlib import Path, PurePosixPath
from shutil import copyfileobj
from stat import S_ISDIR, S_ISREG
from tempfile import NamedTemporaryFile
from uuid import uuid4

from truba_ssh import active_session_client


def project_root(username):
    return PurePosixPath("/arf/scratch", username, "RustMech")


def validate_folder_name(name):
    if not name.strip() or name in (".", "..") or any(char in name for char in ("/", "\\", "\0")):
        raise ValueError("Enter a single folder name.")


@contextmanager
def _project_directory(auth, relative):
    root = project_root(auth.username)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("This folder is outside the RustMech project.")
    with active_session_client(auth).open_sftp() as sftp:
        sftp.get_channel().settimeout(15)
        canonical_root = PurePosixPath(sftp.normalize(str(root)))
        directory = PurePosixPath(sftp.normalize(str(root / relative)))
        if not directory.is_relative_to(canonical_root):
            raise ValueError("This folder is outside the RustMech project.")
        yield sftp, directory


def list_directory(auth, relative):
    with _project_directory(auth, relative) as (sftp, directory):
        entries = sftp.listdir_attr(str(directory))
    return sorted(entries, key=lambda item: (
        not S_ISDIR(item.st_mode or 0), item.filename.casefold(), item.filename
    ))


def create_directory(auth, relative, name):
    validate_folder_name(name)
    with _project_directory(auth, relative) as (sftp, directory):
        sftp.mkdir(str(directory / name))


def _require_missing(sftp, path):
    try:
        sftp.lstat(path)
    except FileNotFoundError:
        return
    raise FileExistsError(path)


def upload_file(auth, relative, source):
    with _project_directory(auth, relative) as (sftp, directory):
        target = str(directory / source.name)
        _require_missing(sftp, target)
        _publish_file(sftp, source, target)


def _publish_file(sftp, source, target):
    temporary = str(PurePosixPath(target).with_name(f".rustmech-upload-{uuid4().hex}.part"))
    with source.open("rb") as local:
        remote = sftp.open(temporary, "wx")
        try:
            with remote:
                remote.set_pipelined(True)
                copyfileobj(local, remote)
            if sftp.stat(temporary).st_size != local.tell():
                raise OSError("Upload size mismatch")
            try:
                # The standard SFTP rename fails if another client created the target.
                sftp.rename(temporary, target)
            except OSError:
                _require_missing(sftp, target)
                raise
        except BaseException:
            with suppress(OSError, EOFError):
                sftp.remove(temporary)
            raise


def _matches_file(sftp, source, target, attrs):
    if not S_ISREG(attrs.st_mode or 0) or attrs.st_size != source.stat().st_size:
        return False
    with source.open("rb") as local, sftp.open(target, "rb") as remote:
        for chunk in iter(lambda: local.read(1024 * 1024), b""):
            if chunk != remote.read(len(chunk)):
                return False
        return not remote.read(1)


def upload_tree(auth, relative, source):
    """Copy the contents of a prepared tree directly into the open folder."""
    with _project_directory(auth, relative) as (sftp, directory):
        pending = []
        # Parents sort before their children. Reject conflicting paths before publishing files.
        for path in sorted(source.rglob("*")):
            target = str(directory / path.relative_to(source).as_posix())
            is_directory = path.is_dir()
            try:
                attrs = sftp.lstat(target)
            except FileNotFoundError:
                pending.append((path, target, is_directory))
                continue
            if (S_ISDIR(attrs.st_mode or 0) if is_directory else _matches_file(sftp, path, target, attrs)):
                continue
            raise FileExistsError(target)

        for path, target, is_directory in pending:
            if is_directory:
                sftp.mkdir(target)
            else:
                _publish_file(sftp, path, target)


def download_file(auth, relative, name, destination):
    if name in ("", ".", "..") or "/" in name or "\0" in name:
        raise ValueError("Select a regular file to download.")
    with _project_directory(auth, relative) as (sftp, directory):
        source = str(directory / name)
        if not S_ISREG(sftp.lstat(source).st_mode or 0):
            raise ValueError("Select a regular file to download.")
        local = NamedTemporaryFile(dir=destination.parent, prefix=".rustmech-download-",
                                   suffix=".part", delete=False)
        temporary = Path(local.name)
        try:
            with local:
                sftp.getfo(source, local, max_concurrent_prefetch_requests=64)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
