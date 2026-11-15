"""Collect wheel-external Netgen DLLs and repair their frozen metadata paths."""

import argparse
import csv
import json
import shutil
from importlib.metadata import distribution
from pathlib import Path


def finalize(target):
    target = target.resolve()
    if not (target / "RustMech.exe").is_file():
        raise ValueError("Expected a built RustMech application directory")
    internal = target / "_internal"
    netgen = internal / "netgen"
    netgen.mkdir(exist_ok=True)
    occt = distribution("netgen-occt")
    copied = []
    for file in occt.files:
        if str(file).lower().endswith(".dll"):
            source = Path(file.locate()).resolve()
            destination = netgen / source.name
            shutil.copy2(source, destination)
            copied.append(destination.name)
    record = internal / f"netgen_occt-{occt.version}.dist-info" / "RECORD"
    if not record.is_file():
        # collect-all(netgen) does not consistently include the separate OCCT
        # distribution metadata. Netgen needs it to locate its runtime DLLs.
        source_metadata = Path(occt.locate_file(record.parent.name))
        shutil.copytree(source_metadata, record.parent, dirs_exist_ok=True)
    with record.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.reader(stream))
    for row in rows:
        if row[0].lower().endswith(".dll"):
            row[0] = f"netgen/{Path(row[0]).name}"
    with record.open("w", newline="", encoding="utf-8") as stream:
        csv.writer(stream).writerows(rows)
    source_libs = Path(distribution("netgen-mesher").locate_file("netgen_mesher.libs"))
    if source_libs.is_dir():
        destination = internal / "netgen_mesher.libs"
        destination.mkdir(exist_ok=True)
        for file in source_libs.glob("*.dll"):
            shutil.copy2(file, destination / file.name)
    report = dict(occt_version=occt.version, occt_dll_count=len(copied),
                  record_paths="netgen/*.dll", uses_windows_icu=True)
    (target / "packaging_manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    finalize(parser.parse_args().directory)
