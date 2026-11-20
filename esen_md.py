"""Build a reproducible Atomsk/LAMMPS job package for the ESEN runner."""

from dataclasses import asdict, dataclass
from fractions import Fraction
import json
from math import isfinite, prod
import os
from pathlib import Path
import random
import re
import shlex
import shutil
import subprocess
from tempfile import TemporaryDirectory
from uuid import uuid4

from esen_md_protocol import ANALYSIS, ENTRY, INITIAL, RESTART, RESUME


ELEMENT = re.compile(r"[A-Z][a-z]?")
MEAM_INDEX = re.compile(r"^\w+\s*\(\s*(\d+(?:\s*,\s*\d+)*)\s*\)\s*=")
MAX_ATOMS = 10_000_000


@dataclass(frozen=True)
class Model:
    target_atoms: int
    repeats: tuple[int, int, int]
    elements: tuple[str, ...]
    counts: tuple[int, ...]
    lattice: float
    temperature: int
    seed: int

    @property
    def atoms(self):
        return sum(self.counts)


def plan_model(composition, target_atoms, lattice, temperature, seed):
    """Use BCC cells with edge lengths differing by at most one repeat."""
    entries = [part.strip().split(":") for part in composition.split(",")]
    elements = tuple(entry[0].strip() for entry in entries)
    if (any(not ELEMENT.fullmatch(element) for element in elements)
            or len(set(elements)) != len(elements)):
        raise ValueError("Elementleri virgülle ayırın ve her sembolü bir kez yazın.")
    if {len(entry) for entry in entries} == {1}:
        fractions = [Fraction(100, len(entries))] * len(entries)
    elif {len(entry) for entry in entries} == {2}:
        try:
            fractions = [Fraction(entry[1].strip().rstrip("%")) for entry in entries]
        except (ValueError, ZeroDivisionError) as error:
            raise ValueError("Bileşimi Hf:20, Nb:20 biçiminde yazın.") from error
        if any(part <= 0 for part in fractions) or sum(fractions) != 100:
            raise ValueError("Atom yüzdeleri pozitif olmalı ve toplamı 100 olmalı.")
    else:
        raise ValueError("Yüzde belirtecekseniz her elementin yüzdesini yazın.")
    if not 2 <= target_atoms <= MAX_ATOMS or not isfinite(lattice) or lattice <= 0:
        raise ValueError("Hedef atom sayısını ve kafes parametresini kontrol edin.")
    if temperature < 1 or not 1 <= seed <= 900_000_000:
        raise ValueError("Sıcaklığı ve rastgele tohumu kontrol edin.")

    side = 1
    while 2 * (side + 1) ** 3 <= target_atoms:
        side += 1
    candidates = [(side,) * (3 - extra) + (side + 1,) * extra for extra in range(4)]
    repeats = min(candidates, key=lambda cell: abs(2 * prod(cell) - target_atoms))
    atoms = 2 * prod(repeats)
    ideal = [atoms * part / 100 for part in fractions]
    counts = [int(part) for part in ideal]
    by_remainder = sorted(range(len(counts)), key=lambda i: ideal[i] - counts[i], reverse=True)
    for index in by_remainder[:atoms - sum(counts)]:
        counts[index] += 1
    if not all(counts):
        raise ValueError("Bu hücrede her element için en az bir atom bulunmalı.")
    return Model(target_atoms, repeats, elements, tuple(counts), lattice, temperature, seed)


def meam_order(text, elements):
    order = tuple(text.replace(",", " ").split())
    if (any(not ELEMENT.fullmatch(element) for element in order)
            or len(set(order)) != len(order) or not set(elements).issubset(order)):
        raise ValueError("MEAM parametre sırasını yazın ve alaşımdaki tüm elementleri ekleyin.")
    return order


def documented_meam_order(library, parameters):
    """Read the documented pair_coeff order when the potential gives one."""
    orders = set()
    for path in (library, parameters):
        if not path.is_file():
            continue
        with path.open(encoding="utf-8-sig", errors="replace") as source:
            for line in source:
                if not (line := line.lstrip()).startswith("#"):
                    continue
                fields = line[1:].split()
                if fields[:3] != ["pair_coeff", "*", "*"]:
                    continue
                order = []
                for field in fields[4:]:
                    if not ELEMENT.fullmatch(field):
                        break
                    order.append(field)
                if order and len(fields) >= len(order) + 6:
                    orders.add(tuple(order))
    return " ".join(next(iter(orders))) if len(orders) == 1 else ""


def _meam_masses(library, parameters, order):
    tokens = shlex.split(library.read_text(encoding="utf-8-sig"), comments=True)
    masses = {}
    if not tokens or len(tokens) % 19:
        raise ValueError("MEAM kütüphanesinde 19 alanlı element kayıtları bekleniyor.")
    for offset in range(0, len(tokens), 19):
        element = tokens[offset]
        if element in order and element not in masses:
            mass = float(tokens[offset + 4])
            if not isfinite(mass) or mass <= 0:
                raise ValueError(f"MEAM kütüphanesindeki atom kütlesi geçersiz: {element}")
            masses[element] = mass
    if missing := set(order).difference(masses):
        raise ValueError(f"MEAM kütüphanesinde bulunamayan elementler: {', '.join(sorted(missing))}")
    assignments = [line.split("#", 1)[0].strip()
                   for line in parameters.read_text(encoding="utf-8-sig").splitlines()]
    if not any("=" in line for line in assignments):
        raise ValueError("MEAM parametre dosyasında parametre ataması bulunamadı.")
    for line in assignments:
        if match := MEAM_INDEX.match(line):
            if any(not 1 <= int(index) <= len(order) for index in match[1].split(",")):
                raise ValueError(f"MEAM parametre indeksini element sırasıyla karşılaştırın: {line}")
    return masses


def _assign_alloy(source, destination, model, masses):
    types = bytearray()
    for atom_type, count in enumerate(model.counts, 1):
        types.extend(bytes([atom_type]) * count)
    random.Random(model.seed).shuffle(types)
    with source.open(encoding="ascii") as original, destination.open("w", encoding="ascii") as output:
        header = []
        for line in original:
            if line.strip() == "Masses":
                break
            header.append(line)
        header = "".join(header)
        declared = re.search(r"^\s*(\d+)\s+atoms\s*$", header, re.MULTILINE)
        if not declared or int(declared[1]) != model.atoms:
            raise ValueError("Atomsk çıktısındaki atom sayısı beklenen değerle uyuşmuyor.")
        header, replaced = re.subn(r"^[ \t]*1[ \t]+atom types[ \t]*$", f"{len(masses)} atom types",
                                   header, flags=re.MULTILINE)
        if replaced != 1:
            raise ValueError("Atomsk çıktısında tek atom tipli başlangıç yapısı bekleniyor.")
        output.write(header)
        output.write("\nMasses\n\n")
        for atom_type, (element, mass) in enumerate(masses.items(), 1):
            output.write(f"{atom_type} {mass:.16g} # {element}\n")
        if not any(line.strip() == "Atoms # atomic" for line in original):
            raise ValueError("Atomsk çıktısında Atoms # atomic bölümü yok.")
        output.write("\nAtoms # atomic\n\n")
        count = 0
        for line in original:
            if not line.strip():
                continue
            atom = line.split()
            count += 1
            if len(atom) != 5 or count > model.atoms or int(atom[0]) != count or atom[1] != "1":
                raise ValueError("Atomsk çıktısında beklenmeyen atom satırı.")
            output.write(f"{count} {types[count - 1]} {' '.join(atom[2:])}\n")
        if count != model.atoms:
            raise ValueError("Atomsk çıktısındaki atom tablosu eksik.")


def atomsk_executable():
    if executable := shutil.which("atomsk"):
        return executable
    for key in ("ProgramFiles", "ProgramFiles(x86)"):
        if folder := os.environ.get(key):
            executable = Path(folder) / "Atomsk" / "atomsk.exe"
            if executable.is_file():
                return str(executable)
    raise ValueError("Atomsk bulunamadı. Yerel Atomsk kurulumunu veya PATH ayarını kontrol edin.")


def prepare_job(parent, model, library, parameters, order, executable):
    """Publish the package only after Atomsk and input validation both succeed."""
    parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".preparing-", dir=parent) as staging:
        scratch = Path(staging)
        package = scratch / "package"
        package.mkdir()
        library = Path(shutil.copyfile(library, package / "meam_kutuphane.meam"))
        parameters = Path(shutil.copyfile(parameters, package / "meam_parametreler.meam"))
        masses = _meam_masses(library, parameters, order)
        raw = "atomsk.lmp"
        arguments = [executable, "--create", "bcc", str(model.lattice), model.elements[0],
                     "-duplicate", *map(str, model.repeats), raw]
        try:
            result = subprocess.run(arguments, cwd=scratch, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as error:
            raise ValueError("Atomsk yapı üretimini 5 dakika içinde tamamlayamadı.") from error
        log = result.stdout.decode(errors="replace")
        if result.returncode or not (scratch / raw).is_file():
            raise ValueError(f"Atomsk yapı üretemedi.\n{log[-2000:]}")
        _assign_alloy(scratch / raw, package / "structure.lmp", model,
                      {element: masses[element] for element in model.elements})
        # Use the existing E20 protocol for every composition in this template.
        run_variables = (f"variable T index {model.temperature}\n"
                         f"variable tohum index {model.seed}\n"
                         "variable cikti_oneki index md-bcc\n")
        analysis = ANALYSIS.replace("@MEAM_ORDER@", " ".join(order))
        analysis = analysis.replace("@ATOM_TYPES@", " ".join(model.elements))
        for filename, content in ((ENTRY, INITIAL), (RESTART, RESUME), ("kosu.inc", run_variables),
                                  ("analiz.inc", analysis), ("atomsk.log", log)):
            (package / filename).write_text(content, encoding="utf-8", newline="\n")
        metadata = dict(asdict(model), atoms=model.atoms, meam_order=order, protocol="e20-bcc-tension-cna-v1")
        (package / "model.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        return package.rename(parent / f"md-bcc-{model.temperature}K-{uuid4().hex[:12]}")
