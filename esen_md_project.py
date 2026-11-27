"""Save the Molecular Dynamics editor state beside its project files."""

from dataclasses import asdict, dataclass, field, replace
import json
from math import isfinite
from pathlib import Path

from PySide6.QtCore import QIODevice, QSaveFile

from esen_md import ELEMENT, MAX_GRAINS, MEAM_SYMBOL


PROJECT_FILE = "rustmech-md.json"


@dataclass(frozen=True)
class MdProject:
    elements: tuple[tuple[str, float], ...] = tuple((symbol, 20.0) for symbol in ("Hf", "Nb", "Ta", "Ti", "Zr"))
    crystal: str = "single"
    dimensions: tuple[float, float, float] = (17.0, 17.0, 17.0)
    grains: int = 12
    lattice: float = 3.4
    temperature: int = 300
    seed: int = 765896
    library: str = ""
    parameters: str = ""
    order: str = ""
    aliases: dict[str, str] = field(default_factory=dict)


def _in_range(value, low, high):
    return type(value) in (int, float) and low <= value <= high and isfinite(value)


def load_md_project(folder):
    try:
        data = json.loads((folder / PROJECT_FILE).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    invalid = "Invalid Molecular Dynamics project settings."
    if not isinstance(data, dict) or data.pop("version", None) != 1:
        raise ValueError(invalid)
    try:
        project = MdProject(**data)
    except TypeError as error:
        raise ValueError(invalid) from error
    if (not isinstance(project.elements, list) or not all(
        isinstance(row, list) and len(row) == 2 and isinstance(row[0], str)
        and ELEMENT.fullmatch(row[0]) and _in_range(row[1], 0, 100) for row in project.elements
    ) or len({symbol for symbol, _ in project.elements}) != len(project.elements)):
        raise ValueError(invalid)
    if (project.crystal not in ("single", "poly")
            or not isinstance(project.dimensions, list) or len(project.dimensions) != 3
            or not all(_in_range(edge, 0.00001, 10_000) for edge in project.dimensions)
            or not _in_range(project.lattice, 0.0001, 100)
            or not all(type(value) is int and low <= value <= high for value, low, high in (
                (project.grains, 2, MAX_GRAINS), (project.temperature, 1, 10_000), (project.seed, 1, 900_000_000)))
            or not all(isinstance(value, str) for value in (project.library, project.parameters, project.order))
            or not isinstance(project.aliases, dict) or not all(
                ELEMENT.fullmatch(element) and isinstance(symbol, str) and MEAM_SYMBOL.fullmatch(symbol)
                for element, symbol in project.aliases.items())):
        raise ValueError(invalid)
    return replace(project, elements=tuple((symbol, share) for symbol, share in project.elements),
                   dimensions=tuple(project.dimensions))


def save_md_project(folder, project):
    payload = json.dumps({"version": 1, **asdict(project)}, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    contents = payload.encode("utf-8")
    target = QSaveFile(str(folder / PROJECT_FILE))
    if not target.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OSError(target.errorString())
    if target.write(contents) != len(contents):
        target.cancelWriting()
        raise OSError(target.errorString())
    if not target.commit():
        raise OSError(target.errorString())


def store_md_path(folder, text):
    if not text:
        return ""
    path = Path(text).resolve()
    return path.relative_to(folder).as_posix() if path.is_relative_to(folder) else str(path)


def resolve_md_path(folder, text):
    if not text:
        return ""
    path = Path(text)
    if not path.is_absolute() and (path.anchor or ".." in path.parts):
        raise ValueError("Invalid Molecular Dynamics project settings.")
    return str(folder / path)
