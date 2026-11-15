"""tools/finalize_distribution.py: sahte paket metadatasiyla, gecici klasorlerde."""
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import finalize_distribution as fd


class _File:
    def __init__(self, name, real):
        self._name, self._real = name, real

    def __str__(self):
        return self._name

    def locate(self):
        return self._real


class _Occt:
    version = "7.8.1"

    def __init__(self, files, root):
        self.files, self._root = files, root

    def locate_file(self, name):
        return self._root / name


class _Mesher:
    def __init__(self, root):
        self._root = root

    def locate_file(self, name):
        return self._root / name


def _read_rows(path):
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.reader(stream))


class FinalizeDistributionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _fake_environment(self):
        src = self.tmp / "site"
        (src / "occt").mkdir(parents=True)
        dll = src / "occt" / "TKernel.dll"
        dll.write_bytes(b"dll")
        txt = src / "occt" / "readme.txt"
        txt.write_text("x")
        meta = src / "netgen_occt-7.8.1.dist-info"
        meta.mkdir()
        with (meta / "RECORD").open("w", newline="", encoding="utf-8") as stream:
            csv.writer(stream).writerows([
                ["netgen_occt.libs/TKernel.dll", "sha256=a", "3"],
                ["netgen_occt-7.8.1.dist-info/METADATA", "sha256=b", "9"],
            ])
        libs = src / "netgen_mesher.libs"
        libs.mkdir()
        (libs / "icu.dll").write_bytes(b"icu")
        (libs / "skip.txt").write_text("no")
        occt = _Occt([_File("netgen_occt.libs/TKernel.dll", dll), _File("occt/readme.txt", txt)], src)
        mesher = _Mesher(src)
        return lambda name: occt if name == "netgen-occt" else mesher

    def _app(self):
        app = self.tmp / "app"
        app.mkdir()
        (app / "RustMech.exe").write_bytes(b"exe")
        (app / "_internal").mkdir()
        return app

    def test_rejects_directory_without_application_executable(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        with self.assertRaisesRegex(ValueError, "built RustMech application"):
            fd.finalize(empty)

    def test_collects_dlls_repairs_record_and_writes_manifest(self):
        app = self._app()
        with patch.object(fd, "distribution", self._fake_environment()):
            fd.finalize(app)
        internal = app / "_internal"
        self.assertTrue((internal / "netgen" / "TKernel.dll").is_file())
        self.assertFalse((internal / "netgen" / "readme.txt").exists())
        self.assertTrue((internal / "netgen_mesher.libs" / "icu.dll").is_file())
        self.assertFalse((internal / "netgen_mesher.libs" / "skip.txt").exists())
        record = internal / "netgen_occt-7.8.1.dist-info" / "RECORD"
        rows = _read_rows(record)
        self.assertEqual(rows[0][0], "netgen/TKernel.dll")
        self.assertEqual(rows[1][0], "netgen_occt-7.8.1.dist-info/METADATA")
        manifest = json.loads((app / "packaging_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["occt_version"], "7.8.1")
        self.assertEqual(manifest["occt_dll_count"], 1)

    def test_running_twice_is_idempotent(self):
        app = self._app()
        fake = self._fake_environment()
        with patch.object(fd, "distribution", fake):
            fd.finalize(app)
            fd.finalize(app)
        record = app / "_internal" / "netgen_occt-7.8.1.dist-info" / "RECORD"
        rows = _read_rows(record)
        self.assertEqual(rows[0][0], "netgen/TKernel.dll")


if __name__ == "__main__":
    unittest.main()
