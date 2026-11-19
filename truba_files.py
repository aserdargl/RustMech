"""SFTP browsing within the authenticated user's RustMech scratch directory."""

from contextlib import contextmanager
from pathlib import PurePosixPath
from stat import S_ISDIR

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
