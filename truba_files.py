"""SFTP file operations within the authenticated user's RustMech project."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager, suppress
from hashlib import file_digest, sha256
from itertools import batched
import json
import ntpath
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shlex
from stat import FILE_ATTRIBUTE_REPARSE_POINT, S_ISDIR, S_ISREG
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import Event, Lock
from uuid import uuid4

from paramiko import SSHException

from remote_operations import BATCH_INPUT_DIRECTORY, batch_geometry_name
from truba_ssh import active_session_client


SYNC_TEMP_FILE = re.compile(r"\.rustmech-(?:upload-[0-9a-f]{32}|download-[a-z0-9_]{8})\.part")


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


def list_batch_folders(auth, relative):
    if relative == PurePosixPath():
        _ensure_project_root(auth)
    return [item.filename for item in list_directory(auth, relative)
            if S_ISDIR(item.st_mode or 0)]


def stage_batch_geometries(auth, sources, *, settings_by_geometry=None, relative=None):
    """Publish face-tagged geometry and optional batch settings without submitting jobs."""
    sources = list(dict.fromkeys(map(Path, sources)))
    if not sources:
        return {}
    selected_directory = relative is not None
    if not selected_directory:
        relative = PurePosixPath(BATCH_INPUT_DIRECTORY)
        _create_batch_input_directory(auth, relative)
    staged = {}
    with _project_directory(auth, relative) as (sftp, directory):
        for source in sources:
            with source.open('rb') as stream:
                digest = file_digest(stream, 'sha256').hexdigest()
            _publish_batch_input(sftp, source, directory / batch_geometry_name(digest), digest)
            staged[str(source)] = {'username': auth.username, 'sha256': digest}
            if selected_directory:
                staged[str(source)]['directory'] = relative.as_posix()
        if settings_by_geometry is not None:
            jobs = [dict(geometry=batch_geometry_name(staged[path]['sha256']), settings=settings)
                    for path, snapshots in settings_by_geometry.items() for settings in snapshots]
            content = json.dumps(dict(schema_version=1, jobs=jobs), sort_keys=True, allow_nan=False).encode('utf-8')
            with TemporaryDirectory(prefix='rustmech-batch-') as temporary:
                manifest = Path(temporary) / 'batch.json'
                manifest.write_bytes(content)
                digest = sha256(content).hexdigest()
                _publish_batch_input(sftp, manifest, directory / f'{digest}.json', digest)
    return staged


def _ensure_project_root(auth):
    with _sftp_channel(auth) as sftp:
        root = str(project_root(auth.username))
        try:
            sftp.stat(root)
        except FileNotFoundError:
            try:
                sftp.mkdir(root)
            except OSError:
                if not S_ISDIR(sftp.stat(root).st_mode or 0):
                    raise


def _create_batch_input_directory(auth, relative):
    _ensure_project_root(auth)
    with _project_directory(auth, PurePosixPath()) as (sftp, root):
        path = str(root / relative)
        try:
            sftp.mkdir(path)
        except OSError:
            if not S_ISDIR(sftp.lstat(path).st_mode or 0):
                raise


def _publish_batch_input(sftp, source, target, digest):
    kind = 'geometry' if source.suffix.lower() == '.vtp' else 'settings'
    target = str(target)
    try:
        attrs = sftp.lstat(target)
    except FileNotFoundError:
        try:
            _publish_file(sftp, source, target)
        except FileExistsError:
            pass  # Another sender may have published the same input.
        attrs = sftp.lstat(target)
    if not S_ISREG(attrs.st_mode or 0):
        raise OSError(f'TRUBA {kind} verification failed: {source.name}')
    with sftp.open(target, 'rb') as uploaded:
        if file_digest(uploaded, 'sha256').hexdigest() != digest:
            raise OSError(f'TRUBA {kind} verification failed: {source.name}')


def _publish_file(sftp, source, target, *, progress=None, sync_source=None, existing=None):
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
            if sync_source is not None:
                if _file_version(os.fstat(local.fileno())) != _file_version(sync_source):
                    raise FileExistsError(str(source))
                mtime = int(sync_source.st_mtime)
                sftp.utime(temporary, (mtime, mtime))
            if existing is not None:
                if _file_version(sftp.lstat(target)) != _file_version(existing):
                    raise FileExistsError(target)
                sftp.posix_rename(temporary, target)
            else:
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
        _download_file(sftp, source, destination)


def read_regular_file(auth, relative, name, *, limit):
    validate_folder_name(name)
    with _project_directory(auth, relative) as (sftp, directory):
        source = str(directory / name)
        if not S_ISREG(sftp.lstat(source).st_mode or 0):
            raise ValueError("Select a regular file on TRUBA.")
        with sftp.open(source, "rb") as stream:
            contents = stream.read(limit + 1)
        if len(contents) > limit:
            raise ValueError("The TRUBA run list is too large.")
        return contents


def require_regular_files(auth, relative, paths):
    with _project_directory(auth, relative) as (sftp, directory):
        checked = {directory}
        for path in paths:
            target = directory / path
            try:
                if target.parent not in checked:
                    if sftp.normalize(str(target.parent)) != str(target.parent):
                        raise ValueError(f"Slurm script folder contains a symbolic link: {path}")
                    checked.add(target.parent)
                if not S_ISREG(sftp.lstat(str(target)).st_mode or 0):
                    raise ValueError(f"Expected a regular Slurm script on TRUBA: {path}")
            except FileNotFoundError as error:
                raise ValueError(f"Slurm script listed in Run.esen is missing on TRUBA: {path}") from error


def _download_file(sftp, source, destination, *, sync_source=None, existing=None):
    local = NamedTemporaryFile(dir=destination.parent, prefix=".rustmech-download-",
                               suffix=".part", delete=False)
    temporary = Path(local.name)
    try:
        with local:
            size = sftp.getfo(source, local, max_concurrent_prefetch_requests=64)
        if sync_source is not None:
            if size != sync_source.st_size:
                raise OSError("Download size mismatch")
            if _file_version(sftp.lstat(source)) != _file_version(sync_source):
                raise FileExistsError(source)
            if (mtime := getattr(sync_source, "st_mtime", None)) is not None:
                os.utime(temporary, (mtime, mtime))
        if existing is not None:
            if _file_version(destination.lstat()) != _file_version(existing):
                raise FileExistsError(str(destination))
            temporary.replace(destination)
        elif sync_source is None:
            temporary.replace(destination)
        else:
            # Both operations fail if a competing transfer creates the destination.
            if os.name == "nt":
                temporary.rename(destination)
            else:
                temporary.hardlink_to(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _sync_name(name):
    if (name in ("", ".", "..") or name.endswith((" ", "."))
            or any(char in '<>:"/\\|?*' or ord(char) < 32 for char in name)
            or (ntpath.isreserved(name) if hasattr(ntpath, "isreserved") else PureWindowsPath(name).is_reserved())):
        raise ValueError(f"Unsupported folder sync path: {name}")
    return name.casefold()


def _sync_kind(attrs):
    if getattr(attrs, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT:
        return None
    if S_ISDIR(attrs.st_mode or 0):
        return "directory"
    if S_ISREG(attrs.st_mode or 0):
        return "file"
    return None


def _file_version(attrs):
    return (_sync_kind(attrs), attrs.st_size, getattr(attrs, "st_mtime_ns", getattr(attrs, "st_mtime", None)))


def _remote_digests(auth, paths):
    """Read hashes over SSH without downloading the corresponding file data."""
    digests = {}
    for batch in batched(paths, 16):
        command = "sha256sum --zero -- " + shlex.join(batch)
        try:
            _, stdout, _ = active_session_client(auth).exec_command(command, timeout=120)
            output = stdout.read()
            if stdout.channel.recv_exit_status() != 0:
                continue
        except (SSHException, OSError, EOFError):
            break
        expected = {path.encode("utf-8"): path for path in batch}
        for line in output.split(b"\0"):
            match = re.fullmatch(rb"([0-9a-f]{64}) [ *](.+)", line)
            if match and match[2] in expected:
                digests[expected[match[2]]] = match[1].decode("ascii")
    return digests


def sync_tree(auth, relative, source):
    """Merge missing or outdated files, with content checks before making changes."""
    if not relative.parts or _sync_kind(source.lstat()) != "directory":
        raise ValueError("Select a local folder and a TRUBA project subfolder.")
    with _project_directory(auth, relative) as (sftp, directory):
        if "esen-folders" in relative.parts or "esen-folders" in directory.parts:
            raise ValueError("Synchronized inputs are read-only.")
        folders = [(source, directory, True, True)]
        create_local, create_remote, uploads, downloads, common = [], [], [], [], []
        while folders:
            local, remote, has_local, has_remote = folders.pop()
            local_entries = {entry.name: entry.lstat() for entry in local.iterdir()} if has_local else {}
            remote_entries = ({entry.filename: entry for entry in sftp.listdir_attr(str(remote))}
                              if has_remote else {})
            # TRUBA permits distinct Linux names that would collide on Windows.
            seen = set()
            for name in sorted(local_entries.keys() | remote_entries.keys()):
                # ESEN snapshots and in-flight transfer files are managed separately.
                if name.casefold() == "esen-folders" or SYNC_TEMP_FILE.fullmatch(name):
                    continue
                key = _sync_name(name)
                if key in seen:
                    raise FileExistsError(str(remote / name))
                seen.add(key)
                local_entry = local_entries.get(name)
                remote_entry = remote_entries.get(name)
                attrs = local_entry if local_entry is not None else remote_entry
                kind = _sync_kind(attrs)
                local_path, remote_path = local / name, remote / name
                if kind is None or (remote_entry is not None and _sync_kind(remote_entry) != kind):
                    raise FileExistsError(str(remote_path))
                if kind == "directory":
                    if local_entry is None:
                        create_local.append(local_path)
                    if remote_entry is None:
                        create_remote.append(str(remote_path))
                    folders.append((local_path, remote_path, local_entry is not None, remote_entry is not None))
                elif remote_entry is None:
                    uploads.append((local_path, str(remote_path), local_entry, None))
                elif local_entry is None:
                    downloads.append((str(remote_path), local_path, remote_entry, None))
                else:
                    common.append((local_path, str(remote_path), local_entry, remote_entry))

        comparable = [path for _, path, local, remote in common if local.st_size == remote.st_size]
        compare_bytes = sum(local.st_size for _, _, local, remote in common if local.st_size == remote.st_size)
        digests = _remote_digests(auth, comparable) if len(comparable) >= 4 or compare_bytes >= 1024 ** 2 else {}
        for local_path, remote_path, local, remote in common:
            if local.st_size == remote.st_size:
                if remote_path in digests:
                    with local_path.open("rb") as stream:
                        same = file_digest(stream, "sha256").hexdigest() == digests[remote_path]
                else:
                    same = _matches_file(sftp, local_path, remote_path, remote)
                if same:
                    continue
            remote_time = getattr(remote, "st_mtime", None)
            local_time = int(local.st_mtime)
            if remote_time is None or local_time == remote_time:
                raise FileExistsError(remote_path)
            if local_time > remote_time:
                uploads.append((local_path, remote_path, local, remote))
            else:
                downloads.append((remote_path, local_path, remote, local))

        for path in create_local:
            path.mkdir()
        for path in create_remote:
            sftp.mkdir(path)
        for path, target, local, existing in uploads:
            if _file_version(path.lstat()) != _file_version(local):
                raise FileExistsError(str(path))
            _publish_file(sftp, path, target, sync_source=local, existing=existing)
        for path, target, remote, existing in downloads:
            _download_file(sftp, path, target, sync_source=remote, existing=existing)
    return len(uploads), len(downloads)
