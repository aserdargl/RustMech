"""Element metadata for MEAM source listings."""

from ast import literal_eval
import csv
from dataclasses import dataclass
from io import StringIO
import re
from urllib.request import urlopen


# NIST Interatomic Potentials Repository's regularly archived LAMMPS records.
NIST_CATALOG = "https://raw.githubusercontent.com/lmhale99/potentials-library/master/potential_LAMMPS.csv"
NIST_ENTRIES = "https://www.ctcms.nist.gov/potentials/entry/"
LAMMPS_FILES = "https://github.com/lammps/lammps/blob/develop/potentials/"
RECORD_ID = re.compile(r"[A-Za-z0-9_-]+")
ELEMENT = re.compile(r"[A-Z][a-z]?")
MAX_CATALOG_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class MeamSource:
    name: str
    elements: frozenset[str]
    url: str


# Parameter files shipped with LAMMPS for pair_style meam. Generic element
# libraries and the separate spline pair styles are not alloy records.
LAMMPS_MEAM = tuple(
    MeamSource(filename, frozenset(elements.split()), LAMMPS_FILES + filename)
    for filename, elements in (
        ("AlSiMgCuFe.meam", "Al Si Mg Cu Fe"),
        ("Bi.meam", "Bi"),
        ("Cu.meam", "Cu"),
        ("MoCoNiVFeAlCr_2nn.meam", "Mo Co Ni V Fe Al Cr"),
        ("Ni.meam", "Ni"),
        ("SiC.meam", "Si C"),
        ("WL.meam", "W"),
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
        entries.append(MeamSource(name, frozenset(elements), url))
    if not entries:
        raise ValueError("No active MEAM records in NIST catalog")
    return tuple(entries)


def load_nist_catalog():
    with urlopen(NIST_CATALOG, timeout=5) as response:
        data = response.read(MAX_CATALOG_BYTES + 1)
    if len(data) > MAX_CATALOG_BYTES:
        raise ValueError("NIST catalog exceeds the size limit")
    return parse_nist_catalog(data.decode("utf-8-sig"))


def matching_sources(catalog, elements):
    selected = frozenset(elements)
    if not selected:
        return []
    return sorted(
        (entry for entry in catalog if selected <= entry.elements),
        key=lambda entry: (len(entry.elements), entry.name, entry.url),
    )
