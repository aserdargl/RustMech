"""SFTP file operations within the authenticated user's RustMech project."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path, PurePosixPath
from stat import S_ISDIR, S_ISREG
from tempfile import NamedTemporaryFile
from threading import Event, Lock
from uuid import uuid4

from paramiko import SSHException

from truba_ssh import active_session_client


@contextmanager
def _sftp_channel(auth):
    with active_session_client(auth).open_sftp() as sftp:
        sftp.get_channel().settimeout(15)
        yield sftp


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
    with _sftp_channel(auth) as sftp:
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


def _publish_file(sftp, source, target, *, progress=None):
    temporary = str(PurePosixPath(target).with_name(f".rustmech-upload-{uuid4().hex}.part"))
    with source.open("rb") as local:
        remote = sftp.open(temporary, "wx")
        try:
            with remote:
                remote.set_pipelined(True)
                for chunk in iter(lambda: local.read(1024 * 1024), b""):
                    remote.write(chunk)
                    if progress is not None:
                        progress(local.tell())
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


def upload_tree(auth, relative, source, *, progress=None):
    """Copy the contents of a prepared tree directly into the open folder."""
    with _project_directory(auth, relative) as (sftp, directory):
        missing_directories = set()
        pending = []
        total = completed = 0
        # Parents sort before their children. Reject conflicting paths before publishing files.
        for path in sorted(source.rglob("*")):
            target = directory / path.relative_to(source).as_posix()
            local = path.stat()
            is_directory = S_ISDIR(local.st_mode)
            size = 0 if is_directory else local.st_size
            total += size
            try:
                attrs = None if target.parent in missing_directories else sftp.lstat(str(target))
            except FileNotFoundError:
                attrs = None
            if attrs is None:
                if is_directory:
                    missing_directories.add(target)
                else:
                    pending.append((path, str(target), size))
                continue
            if (S_ISDIR(attrs.st_mode or 0) if is_directory else _matches_file(sftp, path, str(target), attrs)):
                completed += size
                continue
            raise FileExistsError(str(target))

        if progress is not None:
            progress(completed, total)
        for target in sorted(missing_directories):
            sftp.mkdir(str(target))
        with ExitStack() as stack:
            channels = [sftp]
            if len(pending) >= 4 or (len(pending) > 1 and total - completed >= 32 * 1024 * 1024):
                for _ in range(min(3, len(pending)) - 1):
                    try:
                        channels.append(stack.enter_context(_sftp_channel(auth)))
                    except (SSHException, OSError, EOFError):
                        # A server channel limit should not prevent a sequential transfer.
                        break
            _publish_pending(channels, pending, progress, completed, total)


def _publish_pending(channels, pending, progress, completed, total):
    # Start larger files first to keep the channels busy for more of the transfer.
    files = iter(sorted(pending, key=lambda item: item[2], reverse=True) if len(channels) > 1 else pending)
    lock = Lock()
    interrupted = Event()

    def publish(sftp):
        try:
            while not interrupted.is_set():
                with lock:
                    job = next(files, None)
                if job is None:
                    break
                path, target, _size = job
                previous = 0

                def report(sent):
                    nonlocal completed, previous
                    with lock:
                        completed += sent - previous
                        previous = sent
                        progress(completed, total)

                _publish_file(sftp, path, target, progress=report if progress is not None else None)
        except BaseException:
            interrupted.set()
            raise

    if len(channels) == 1:
        publish(channels[0])
        return
    with ThreadPoolExecutor(max_workers=len(channels), thread_name_prefix="truba-upload") as executor:
        tasks = [executor.submit(publish, channel) for channel in channels]
        for task in tasks:
            task.result()


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
