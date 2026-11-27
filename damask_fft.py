"""Periodic linear-elastic homogenization with an external DAMASK grid solver."""

import base64
import csv
import json
import os
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import sys
from dataclasses import asdict
from tempfile import mkdtemp

import numpy as np

from analysis_process import AnalysisProcess, report_analysis_step
from mesh_resources import require_memory
from model import ROOT, validate_geometry, write_json
from periodic_sampling import has_repeating_cell, sample_box


COMPONENTS = ("xx", "yy", "zz", "yz", "xz", "xy")
TENSOR_INDICES = ((0, 0), (1, 1), (2, 2), (1, 2), (0, 2), (0, 1))
STRAIN = 1e-4
MIN_GRID = 8
RESULT_FILE = "stiffness_mpa.csv"
CANCEL_FILE = ".cancel"

# Run beside the DAMASK binary, using the Linux HDF5 reader. A cancellation
# marker survives termination of the Windows worker and covers startup races.
_LINUX_DRIVER = """
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import sys

try:
    import h5py
except ImportError:
    sys.exit('Install python3-h5py in the DAMASK Linux environment.')
import numpy as np

case = sys.argv[1]
cancel = Path('.cancel')
solver = sys.argv[2] if len(sys.argv) > 2 else (shutil.which('damask_grid') or shutil.which('DAMASK_grid'))
if solver is None:
    sys.exit('Install the DAMASK grid solver in this Linux environment.')
if cancel.exists():
    sys.exit('DAMASK analysis cancelled.')
# The Fortran VTI reader uses stack buffers that grow with the grid.
soft_stack, hard_stack = resource.getrlimit(resource.RLIMIT_STACK)
if soft_stack != hard_stack:
    resource.setrlimit(resource.RLIMIT_STACK, (hard_stack, hard_stack))
with open(case + '.log', 'w') as log:
    child = subprocess.Popen(
        [solver, '--load', case + '.yaml', '--geom', 'cell.vti', '--material',
         'material.yaml', '--numerics', 'numerics.yaml', '--jobname', case],
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    while True:
        try:
            code = child.wait(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            if cancel.exists():
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
                sys.exit('DAMASK analysis cancelled.')
if cancel.exists():
    sys.exit('DAMASK analysis cancelled.')
if code:
    sys.exit(f'DAMASK did not complete {case} (exit {code}). See {case}.log.')
steps = [line.split() for line in Path(case + '.sta').read_text().splitlines()[1:] if line.strip()]
if not steps or steps[-1][0] != '1' or abs(float(steps[-1][1]) - 1.0) > 1e-9 or steps[-1][3] != 'T':
    sys.exit(f'DAMASK did not converge for {case}. See {case}.log.')
with open(case + '.log') as log:
    version = next((line.split(':', 1)[1].strip() for line in log
                    if line.lstrip().startswith('Version:')), 'unknown')
with h5py.File(case + '.hdf5') as result:
    if 'increment_1' not in result:
        sys.exit(f'DAMASK did not converge for {case}. See {case}.log.')
    phases = result['increment_1/phase']
    stress_sum = np.zeros((3, 3))
    count = 0
    for phase in phases.values():
        stress = phase['mechanical/P'][:]
        stress_sum += stress.sum(axis=0)
        count += len(stress)
    print(json.dumps({'stress_pa': (stress_sum / count).tolist(), 'voxels': count,
                      'iterations': sum(int(step[4]) for step in steps),
                      'cutback_level': max(int(step[2]) for step in steps),
                      'version': version}))
"""


class DamaskAnalysisProcess(AnalysisProcess):
    def __init__(self, settings, grid, void_ratio, distribution, *, folder=None):
        self.folder = new_run_folder() if folder is None else Path(folder)
        super().__init__(run_periodic_fft, settings, grid, void_ratio, distribution, self.folder)

    def force_stop(self):
        (self.folder / CANCEL_FILE).touch()
        self._process.join(timeout=3)
        super().force_stop()
        if not (self.folder / RESULT_FILE).exists():
            write_json(self.folder / "status.json", {"state": "cancelled"})


def new_run_folder():
    root = ROOT / "results"
    root.mkdir(exist_ok=True)
    return Path(mkdtemp(prefix="FFT_DAMASK_", dir=root))


def whole_fft_periods(periods):
    """Identify axis lengths that contain a positive whole number of cells."""
    nearest = np.rint(periods)
    return (nearest >= 1) & np.isclose(periods, nearest, rtol=0, atol=1e-8)


def validate_periodic_fft(settings):
    if settings.imported_geometry_path or settings.test_profile != "custom" or not has_repeating_cell(settings):
        return ["FFT requires a procedural periodic TPMS or lattice cell."]
    errors = validate_geometry(settings, include_mesh=False)
    if not errors and (settings.size_x is not None or settings.size_y is not None):
        periods = np.asarray(settings.dimensions) / settings.cell_size
        if not whole_fft_periods(periods).all():
            errors.append("For periodic FFT, each outer dimension must be a whole multiple of the cell period.")
    if not np.isfinite(settings.young_modulus) or settings.young_modulus <= 0:
        errors.append("FFT requires a positive Young's modulus.")
    if not -1 < settings.poisson_ratio < 0.5:
        errors.append("FFT requires a Poisson ratio between -1 and 0.5.")
    return errors


def fft_dimensions(settings):
    if settings.size_x is None and settings.size_y is None:
        return (settings.cell_size,) * 3
    return settings.dimensions


def sample_periodic_cell(settings, grid):
    """Return the DAMASK solid phase at the centers of the periodic voxels."""
    _validate_grid(grid)
    # The float32 field and boolean phase mask overlap during thresholding.
    require_memory(grid**3 * 5, "DAMASK voxel sampling")
    from mesh_engine import geometry_threshold

    lengths = fft_dimensions(settings)
    axes = [(np.arange(grid) + 0.5) * (length / grid) for length in lengths]
    field = sample_box(settings, axes, dtype=np.float32,
                       threshold=geometry_threshold(settings))
    return field < 0


def run_periodic_fft(settings, grid, void_ratio, distribution, folder, *, on_progress=report_analysis_step):
    folder = Path(folder)
    try:
        errors = validate_periodic_fft(settings)
        if errors:
            raise ValueError("\n".join(errors))
        on_progress("Sampling the periodic cell…")
        solid = sample_periodic_cell(settings, grid)
        lengths = fft_dimensions(settings)
        size_mm = lengths[0] if len(set(lengths)) == 1 else lengths
        return solve_cell(solid, size_mm, settings.young_modulus,
                          settings.poisson_ratio, void_ratio, distribution, folder,
                          settings_snapshot=asdict(settings), on_progress=on_progress)
    except Exception as exc:
        state = "cancelled" if (folder / CANCEL_FILE).exists() else "failed"
        write_json(folder / "status.json", {"state": state, "error": str(exc)})
        raise RuntimeError(f"{exc}\n{folder}") from exc


def _validate_grid(grid):
    if not isinstance(grid, int) or grid < MIN_GRID:
        raise ValueError(f"FFT grid must contain at least {MIN_GRID} voxels per axis.")


def solve_cell(solid, size_mm, young_mpa, poisson, void_ratio, distribution, folder,
               *, settings_snapshot=None, on_progress=report_analysis_step):
    """Solve six independent engineering strain modes using paired perturbations."""
    solid = np.asarray(solid, dtype=bool)
    if solid.ndim != 3 or len(set(solid.shape)) != 1:
        raise ValueError("DAMASK FFT expects a cubic voxel grid.")
    grid = solid.shape[0]
    _validate_grid(grid)
    if not solid.any():
        raise ValueError("The FFT grid contains no solid voxels. Increase the grid resolution.")
    if not 0 < void_ratio < 1 or not np.all(np.isfinite(size_mm)) or np.any(np.asarray(size_mm) <= 0):
        raise ValueError("A positive cell size and a void stiffness ratio between 0 and 1 are required.")
    if not np.isfinite(young_mpa) or young_mpa <= 0 or not -1 < poisson < 0.5:
        raise ValueError("Invalid isotropic elastic constants.")
    folder = Path(folder)
    _write_geometry(folder / "cell.vti", solid, np.asarray(size_mm) * 1e-3)
    _write_material(folder / "material.yaml", young_mpa * 1e6, poisson, void_ratio, solid.all())
    (folder / "numerics.yaml").write_text(
        "solver:\n  grid:\n    FFTW_plan_mode: FFTW_ESTIMATE\n"
        "    mechanical:\n      N_iter_max: 600\n      eps_rel_div(P): 1.0e-6\n",
        encoding="utf-8",
    )
    metadata = {
        "solver": "DAMASK grid", "method": "spectral_basic", "grid": grid,
        "size_mm": size_mm, "solid_volume_fraction": float(solid.mean()),
        "young_mpa": young_mpa, "poisson_ratio": poisson, "void_stiffness_ratio": void_ratio,
        "wsl_distribution": distribution if os.name == "nt" else None,
        "engineering_strain_amplitude": STRAIN, "components": COMPONENTS,
        "settings": settings_snapshot, "load_cases": {},
    }
    write_json(folder / "inputs.json", metadata)
    stiffness = np.empty((6, 6))
    for column, (label, (i, j)) in enumerate(zip(COMPONENTS, TENSOR_INDICES)):
        message = f"DAMASK · {column + 1}/{len(COMPONENTS)} · {label}"
        if on_progress is report_analysis_step:
            report_analysis_step(message, column, len(COMPONENTS))
        else:
            on_progress(message)
        stresses = []
        for suffix, sign in (("plus", 1), ("minus", -1)):
            if (folder / CANCEL_FILE).exists():
                raise RuntimeError("DAMASK analysis cancelled.")
            deformation = np.eye(3)
            deformation[i, j] += sign * STRAIN / (1 if i == j else 2)
            if i != j:
                deformation[j, i] += sign * STRAIN / 2
            case = f"{label}_{suffix}"
            _write_load(folder / f"{case}.yaml", deformation)
            payload = _solve_load(folder, distribution, case)
            stress = np.asarray(payload["stress_pa"])
            if payload["voxels"] != solid.size or stress.shape != (3, 3) or not np.isfinite(stress).all():
                raise RuntimeError(f"Invalid DAMASK stress output for {case}.")
            metadata["damask_version"] = payload["version"]
            metadata["load_cases"][case] = {
                "converged": True, "iterations": payload["iterations"],
                "cutback_level": payload["cutback_level"], "log": f"{case}.log",
            }
            write_json(folder / "inputs.json", metadata)
            stresses.append((stress + stress.T) / 2)
        derivative = (stresses[0] - stresses[1]) / (2 * STRAIN * 1e6)
        stiffness[:, column] = [derivative[a, b] for a, b in TENSOR_INDICES]

    if (folder / CANCEL_FILE).exists():
        raise RuntimeError("DAMASK analysis cancelled.")
    denominator = np.linalg.norm(stiffness)
    asymmetry = np.linalg.norm(stiffness - stiffness.T) / denominator if denominator else np.inf
    if not np.isfinite(asymmetry) or asymmetry > 0.01:
        raise RuntimeError(f"DAMASK stiffness failed the reciprocity check ({asymmetry:.3g}).")
    tmp = folder / "stiffness.tmp"
    with tmp.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["MPa", *COMPONENTS])
        writer.writerows((label, *row) for label, row in zip(COMPONENTS, stiffness))
    metadata.update(state="complete", relative_asymmetry=float(asymmetry),
                    stiffness_mpa=stiffness.tolist(), directory=str(folder))
    status_tmp = folder / "status.tmp"
    write_json(status_tmp, metadata)
    if (folder / CANCEL_FILE).exists():
        raise RuntimeError("DAMASK analysis cancelled.")
    tmp.replace(folder / RESULT_FILE)
    status_tmp.replace(folder / "status.json")
    return metadata


def _write_geometry(path, solid, size_m):
    if solid.size * 4 > np.iinfo(np.uint32).max:
        raise ValueError("This grid exceeds the DAMASK VTI material data size supported by RustMech.")
    # The encoded VTI text and its binary input coexist during export.
    require_memory(solid.size * 24, "DAMASK geometry export")
    n = solid.shape[0]
    extent = f"0 {n} 0 {n} 0 {n}"
    spacing = " ".join(str(length / n) for length in np.broadcast_to(size_m, (3,)))
    materials = (~solid).astype("<i4").tobytes(order="F")
    binary = base64.b64encode(struct.pack("<I", len(materials)) + materials).decode("ascii")
    path.write_text(
        '<?xml version="1.0"?>\n'
        '<VTKFile type="ImageData" version="1.0" byte_order="LittleEndian" header_type="UInt32">\n'
        f'<ImageData WholeExtent="{extent}" Origin="0 0 0" Spacing="{spacing}">\n'
        f'<Piece Extent="{extent}"><CellData>\n'
        '<DataArray type="Int32" Name="material" format="binary">\n'
        f' {binary}\n'
        '</DataArray>\n</CellData>\n</Piece>\n</ImageData>\n</VTKFile>\n',
        encoding="utf-8",
    )


def _write_material(path, young, poisson, void_ratio, homogeneous):
    phases = {"Solid": young}
    if not homogeneous:
        phases["SoftVoid"] = young * void_ratio
    lines = ["homogenization:", "  Cell:", "    N_constituents: 1",
             "    mechanical: {type: pass}", "phase:"]
    for name, modulus in phases.items():
        mu = modulus / (2 * (1 + poisson))
        lam = 2 * mu * poisson / (1 - 2 * poisson)
        lines.extend([f"  {name}:", "    lattice: cF", "    mechanical:", "      output: [P]",
                      "      elastic:", "        type: Hooke",
                      f"        C_11: {lam + 2 * mu:.16e}", f"        C_12: {lam:.16e}",
                      f"        C_44: {mu:.16e}"])
    lines.append("material:")
    for name in phases:
        lines.extend(["  - homogenization: Cell",
                      f"    constituents: [{{phase: {name}, v: 1.0, O: [1.0, 0.0, 0.0, 0.0]}}]"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_load(path, deformation):
    path.write_text(
        "solver: {mechanical: spectral_basic}\nloadstep:\n"
        "  - discretization: {t: 1.0, N: 1}\n    f_out: 1\n"
        f"    boundary_conditions: {{mechanical: {{F: {json.dumps(deformation.tolist())}}}}}\n",
        encoding="utf-8",
    )


def damask_grid_executable():
    private_bin = Path(sys.prefix) / "damask" / "bin"
    for name in ("damask_grid", "DAMASK_grid"):
        executable = shutil.which(str(private_bin / name))
        if executable:
            return executable
    for name in ("damask_grid", "DAMASK_grid"):
        executable = shutil.which(name)
        if executable:
            return executable
    raise RuntimeError("DAMASK grid solver was not found")


def _solve_load(folder, distribution, case):
    # WSL --cd accepts a Windows absolute path. Quote the Linux argv once for
    # bash, since wsl.exe reconstructs a command line from the Windows argv.
    argv = ["env", "OMP_NUM_THREADS=2", "python3", "-c", _LINUX_DRIVER, case]
    if os.name == "nt":
        command = ["wsl.exe", "-d", distribution, "--cd", str(folder.resolve()),
                   "--", "bash", "-c", shlex.join(argv)]
        flags = subprocess.CREATE_NO_WINDOW
    elif distribution is None:
        command = [sys.executable, "-c", _LINUX_DRIVER, case, damask_grid_executable()]
        flags = 0
    else:
        command, flags = argv, 0
    completed = subprocess.run(command, cwd=folder, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", creationflags=flags,
                               env={**os.environ, "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS", "2")})
    if completed.returncode:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(message or f"DAMASK exited with code {completed.returncode}.")
    return json.loads(completed.stdout)
