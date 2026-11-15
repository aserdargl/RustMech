"""Measure actual spawned preview latency, including surface work and IPC."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from threading import Event
from time import perf_counter

from mesh_preview_worker import run_mesh_preview
from model import Settings


def main():
    report = []
    cached = None
    for label, settings in [
        ("cold_preview", Settings()),
        ("mesh_size_change", replace(Settings(), mesh_resolution=0.1)),
        (
            "cell_size_change",
            replace(Settings(), mesh_resolution=0.1, cell_size=2.5),
        ),
    ]:
        stages = []
        start = perf_counter()

        def on_progress(message):
            stages.append(dict(stage=message, elapsed_seconds=perf_counter() - start))

        cached = run_mesh_preview(settings, cached, on_progress, Event())
        row = dict(
            case=label,
            elapsed_seconds=perf_counter() - start,
            elements=cached.mesh.grid.n_cells,
            surface_triangles=cached.surface.n_cells,
            stages=stages,
        )
        report.append(row)
        print(json.dumps(row), flush=True)
    Path("preview_benchmark.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
