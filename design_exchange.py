"""Versioned local design exchange. Vendored unchanged in both desktop apps.

Each publication is an immutable directory, made visible by one atomic rename.
No running application, server, or neighbouring source checkout is required.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
from uuid import UUID, uuid4

SCHEMA_VERSION = 1


def pool_directory():
    override = os.environ.get("RUSTMECH_DESIGN_POOL")
    if override:
        return Path(override).expanduser().resolve()
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    return base / "Gitterburg-RustMech" / "DesignPool"


def file_digest(path):
    digest = sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def publish_design(name, write_geometry, parameters=None, root=None):
    """The writer must create a complete, millimetre STL at the supplied path."""
    root = Path(root) if root is not None else pool_directory()
    root.mkdir(parents=True, exist_ok=True)
    identifier = uuid4().hex
    temporary = root / (".incoming-" + identifier)
    temporary.mkdir()
    try:
        geometry = temporary / "geometry.stl"
        write_geometry(geometry)
        if not geometry.is_file() or geometry.stat().st_size < 84:
            raise ValueError("No complete geometry was produced for transfer.")
        manifest = dict(schema_version=SCHEMA_VERSION, id=identifier,
                        name=str(name).strip() or "Gitterburg design", source="Gitterburg",
                        created_at=datetime.now(timezone.utc).isoformat(), units="mm",
                        geometry="geometry.stl", sha256=file_digest(geometry),
                        parameters=parameters or {})
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=True, allow_nan=False, indent=2), encoding="utf-8")
        temporary.replace(root / identifier)
        return manifest
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def read_design(identifier, root=None, verify=False):
    root = Path(root) if root is not None else pool_directory()
    if UUID(identifier).hex != identifier:
        raise ValueError("Invalid design identifier.")
    directory = root / identifier
    data = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if (data.get("schema_version") != SCHEMA_VERSION or data.get("id") != identifier
            or data.get("units") != "mm" or data.get("geometry") != "geometry.stl"):
        raise ValueError("Unsupported design package or units.")
    geometry = directory / "geometry.stl"
    if not geometry.is_file():
        raise ValueError("Design geometry is missing.")
    if verify and file_digest(geometry) != data.get("sha256"):
        raise ValueError("Design geometry changed after transfer. Send the design again.")
    return data, geometry


def list_designs(root=None):
    """Return usable manifests and visible diagnostics for incomplete packages."""
    root = Path(root) if root is not None else pool_directory()
    entries, errors = [], []
    if root.exists():
        for directory in root.iterdir():
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            try:
                data, _ = read_design(directory.name, root)
                entries.append(data)
            except (OSError, ValueError, KeyError, TypeError) as error:
                errors.append(f"{directory.name}: {error}")
    return sorted(entries, key=lambda item: item.get("created_at", ""), reverse=True), errors
