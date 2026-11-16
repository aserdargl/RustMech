"""Test paketi: testler gercek DoEResults klasorune yazmasin."""
import atexit
import os
import shutil
import tempfile

if "RUSTMECH_DOE_RESULTS" not in os.environ:
    _results = tempfile.mkdtemp(prefix="rustmech-doe-results-")
    os.environ["RUSTMECH_DOE_RESULTS"] = _results
    atexit.register(shutil.rmtree, _results, ignore_errors=True)
