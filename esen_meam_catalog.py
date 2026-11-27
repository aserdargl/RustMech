"""MEAM source listings and paired file downloads."""

from ast import literal_eval
import csv
from dataclasses import dataclass
from io import StringIO
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from urllib.parse import quote
from urllib.request import urlopen
from uuid import uuid4

from esen_md import ELEMENT, MEAM_SYMBOL


# NIST Interatomic Potentials Repository's regularly archived LAMMPS records.
NIST_ARCHIVE = "https://raw.githubusercontent.com/lmhale99/potentials-library/master/potential_LAMMPS"
NIST_CATALOG = NIST_ARCHIVE + ".csv"
NIST_ENTRIES = "https://www.ctcms.nist.gov/potentials/entry/"
LAMMPS_FILES = "https://github.com/lammps/lammps/blob/develop/potentials/"
LAMMPS_DOWNLOADS = "https://raw.githubusercontent.com/lammps/lammps/develop/potentials/"
RECORD_ID = re.compile(r"[A-Za-z0-9_-]+")
FILENAME = re.compile(r"[A-Za-z0-9_+][A-Za-z0-9_.+-]*")
WINDOWS_DEVICES = {"CON", "PRN", "AUX", "NUL", *(f"{kind}{number}" for kind in ("COM", "LPT") for number in range(10))}
MAX_SOURCE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class MeamSource:
    name: str
    elements: frozenset[str]
    url: str
    record_id: str
    library: str = ""
    order: tuple[str, ...] = ()
    aliases: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class DownloadedMeam:
    library: Path
    parameters: Path
    order: tuple[str, ...]
    aliases: dict[str, str]


# Parameter files shipped with LAMMPS for pair_style meam. Generic element
# libraries and the separate spline pair styles are not alloy records.
LAMMPS_MEAM = tuple(
    MeamSource(filename, frozenset(elements.split()), LAMMPS_FILES + filename, filename.removesuffix(".meam"),
               library, tuple(order.split()), tuple((element, symbol) for element, symbol
                                                    in zip(elements.split(), order.split()) if element != symbol))
    for filename, library, elements, order in (
        ("AlSiMgCuFe.meam", "library.meam", "Al Si Mg Cu Fe", "AlS SiS MgS CuS FeS"),
        ("Bi.meam", "library.meam", "Bi", "Bi"),
        ("Cu.meam", "library.meam", "Cu", "Cu"),
        ("MoCoNiVFeAlCr_2nn.meam", "library_2nn.meam", "Mo Co Ni V Fe Al Cr", "Mo Co Ni V Fe Al Cr"),
        ("Ni.meam", "library.meam", "Ni", "Ni"),
        ("SiC.meam", "library.meam", "Si C", "Si C"),
        ("WL.meam", "library.meam", "W", "WL"),
    )
)


def parse_nist_catalog(contents):
    rows = csv.DictReader(StringIO(contents, newline=""))
    required = {"id", "potid", "elements", "pair_style", "status"}
    if not required.issubset(rows.fieldnames or ()):
        raise ValueError("Unexpected NIST catalog format")
    entries = []
    for row in rows:
        if row["pair_style"] != "meam" or row["status"] != "active":
            continue
        record, potential = row["id"], row["potid"]
        if not (RECORD_ID.fullmatch(record or "") and RECORD_ID.fullmatch(potential or "")):
            continue
        try:
            elements = literal_eval(row["elements"] or "[]")
        except (SyntaxError, ValueError):
            continue
        if not isinstance(elements, list) or not elements or not all(
            isinstance(element, str) and ELEMENT.fullmatch(element) for element in elements
        ):
            continue
        url = f"{NIST_ENTRIES}{potential}/{record}.html"
        name = record.partition("--LAMMPS--")[0].replace("--", " · ")
        entries.append(MeamSource(name, frozenset(elements), url, record))
    if not entries:
        raise ValueError("No active MEAM records in NIST catalog")
    return tuple(entries)


def _read_source(url):
    with urlopen(url, timeout=8) as response:
        data = response.read(MAX_SOURCE_BYTES + 1)
    if not data.strip() or len(data) > MAX_SOURCE_BYTES or data.lstrip().startswith(b"<"):
        raise ValueError("Unexpected MEAM source file contents.")
    return data


def load_nist_catalog():
    return parse_nist_catalog(_read_source(NIST_CATALOG).decode("utf-8-sig"))


def _nist_pair(source):
    prefix = f"{NIST_ARCHIVE}/{source.record_id}"
    payload = _read_source(prefix + ".json")
    try:
        record = json.loads(payload)["potential-LAMMPS"]
        terms = record["pair_coeff"]["term"]
        library, parameters = terms[0]["file"], terms[2]["file"]
        order = tuple(terms[1]["option"].split())
        atoms = record["atom"]
        if isinstance(atoms, dict):
            atoms = [atoms]
        aliases = {atom["element"]: atom.get("symbol") or atom["element"] for atom in atoms
                   if atom["element"] in source.elements}
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as error:
        raise ValueError("The source does not specify a MEAM library and parameter pair.") from error
    return prefix + "/", library, parameters, order, aliases


def download_meam_source(source, project):
    """Publish both files together in a new directory under the chosen project."""
    if not project.is_dir():
        raise ValueError("Choose an existing project folder.")
    if not RECORD_ID.fullmatch(source.record_id):
        raise ValueError("The source does not specify a MEAM library and parameter pair.")
    if source.library:
        prefix, library, parameters = LAMMPS_DOWNLOADS, source.library, source.name
        order, aliases = source.order, dict(source.aliases)
    else:
        prefix, library, parameters, order, aliases = _nist_pair(source)
    filenames = (library, parameters)
    if (not all(isinstance(name, str) and FILENAME.fullmatch(name) and not name.endswith(".")
                and name.split(".")[0].upper() not in WINDOWS_DEVICES for name in filenames)
            or library.casefold() == parameters.casefold()):
        raise ValueError("The source does not specify a MEAM library and parameter pair.")
    symbols = {aliases.get(element, element) for element in source.elements}
    if (not all(isinstance(symbol, str) and MEAM_SYMBOL.fullmatch(symbol) for symbol in order)
            or not symbols.issubset(order) or len(symbols) != len(source.elements)
            or len(set(order)) != len(order)):
        raise ValueError("The source does not specify a usable MEAM element order.")
    payloads = [_read_source(prefix + quote(name, safe="")) for name in filenames]
    parent = project / "meam-potentials"
    if not parent.resolve().is_relative_to(project.resolve()):
        raise ValueError("The MEAM folder is outside the project folder.")
    parent.mkdir(exist_ok=True)
    target = parent / f"{source.record_id}-{uuid4().hex[:12]}"
    with TemporaryDirectory(prefix=".downloading-", dir=parent) as staging:
        pair = Path(staging) / "potential"
        pair.mkdir()
        for name, data in zip(filenames, payloads):
            (pair / name).write_bytes(data)
        pair.rename(target)
    return DownloadedMeam(target / library, target / parameters, order,
                          {element: symbol for element, symbol in aliases.items() if element != symbol})


def matching_sources(catalog, elements):
    selected = frozenset(elements)
    if not selected:
        return []
    return sorted(
        (entry for entry in catalog if selected <= entry.elements),
        key=lambda entry: (len(entry.elements), entry.name, entry.url),
    )
