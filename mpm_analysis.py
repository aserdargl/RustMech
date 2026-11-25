"""Three-dimensional TPMS compression using the Kratos MPM application."""

from dataclasses import asdict, dataclass, fields
import csv
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import numpy as np

from model import require_geometry


@dataclass(frozen=True)
class MPMSettings:
    stroke_mm: float = 0.3
    duration_ms: float = 0.12
    grid_mm: float = 0.5
    points_per_axis: int = 2
    young_mpa: float = 2000.0
    poisson_ratio: float = 0.3
    density_kg_m3: float = 1200.0

    def validate(self, geometry):
        if geometry.test_profile != 'custom' or geometry.imported_geometry_path or geometry.structure_family != 'tpms':
            raise ValueError('MPM requires a generated TPMS specimen in the Model workspace.')
        require_geometry(geometry, include_mesh=False)
        for item in fields(self):
            value = getattr(self, item.name)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f'Invalid MPM input: {item.name}.')
        if not (0 < self.stroke_mm < geometry.size and self.duration_ms > 0 and self.grid_mm > 0):
            raise ValueError('MPM requires positive spacing and duration, and a stroke below the specimen height.')
        if type(self.points_per_axis) is not int or not 1 <= self.points_per_axis <= 4:
            raise ValueError('Use 1–4 material points per grid-cell axis.')
        if not (self.young_mpa > 0 and self.density_kg_m3 > 0 and 0 <= self.poisson_ratio < 0.49):
            raise ValueError('Use positive Young’s modulus and density and a Poisson ratio in [0, 0.49).')


CURVE_NAME = 'mpm_force_displacement.csv'
SUMMARY_NAME = 'mpm_summary.json'
PARTICLES_NAME = 'mpm_final_particles.vtp'
CURVE_FIELDS = ('time_s', 'displacement_mm', 'upper_force_n', 'lower_force_n', 'kinetic_energy_j')
HEX_CORNERS = ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
               (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1))


def read_mpm_result(directory):
    directory = Path(directory)
    summary = json.loads((directory / SUMMARY_NAME).read_text(encoding='utf-8'))
    if not isinstance(summary, dict) or summary.get('format') != 'rustmech-mpm' or summary.get('version') != 1:
        raise ValueError('Unsupported MPM result format.')
    with (directory / CURVE_NAME).open(newline='', encoding='utf-8') as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != CURVE_FIELDS:
            raise ValueError('Invalid MPM force–displacement columns.')
        curve = np.asarray([[float(row[key]) for key in CURVE_FIELDS] for row in reader])
    if (len(curve) < 2 or not np.all(np.isfinite(curve)) or np.any(np.diff(curve[:, 0]) <= 0)
            or np.any(np.diff(curve[:, 1]) < 0) or np.any(curve[:, 2:] < 0)
            or not math.isclose(curve[-1, 1], summary['mpm']['stroke_mm'], rel_tol=1e-9)):
        raise ValueError('Incomplete or invalid MPM force–displacement history.')
    return summary, curve


def _write_hex_model(path, name, cells, spacing, origin, *, material):
    """Write the body quadrature cells or the independent background grid."""
    corners = cells[:, None, :] + np.asarray(HEX_CORNERS)
    nodes, inverse = np.unique(corners.reshape(-1, 3), axis=0, return_inverse=True)
    nodes = origin + nodes * spacing
    element = 'MPMUpdatedLagrangian3D8N' if material else 'Element3D8N'
    with path.open('w', encoding='ascii', newline='\n') as out:
        out.write('Begin Properties 1\nEnd Properties\nBegin Nodes\n')
        for index, (x, y, z) in enumerate(nodes, 1):
            out.write(f'{index} {x:.17g} {y:.17g} {z:.17g}\n')
        out.write(f'End Nodes\nBegin Elements {element}\n')
        for index, row in enumerate(inverse.reshape(-1, 8) + 1, 1):
            connectivity = ' '.join(map(str, row))
            out.write(f'{index} 1 {connectivity}\n')
        out.write(f'End Elements\nBegin SubModelPart {name}\n')
        for kind, count in (('Nodes', len(nodes)), ('Elements', len(cells))):
            out.write(f'Begin SubModelPart{kind}\n')
            out.writelines(f'{index}\n' for index in range(1, count + 1))
            out.write(f'End SubModelPart{kind}\n')
        out.write('End SubModelPart\n')


def prepare_mpm_case(geometry, settings, directory):
    from mesh_engine import geometry_threshold
    from periodic_sampling import sample_box

    settings.validate(geometry)
    lengths = np.asarray(geometry.dimensions)
    n = np.maximum(2, np.ceil(lengths / settings.grid_mm).astype(int))
    n_points = n * settings.points_per_axis
    # Bound the MDPA, particle search and explicitly integrated workload before allocating.
    if n.max() > 60 or n_points.max() > 100:
        raise ValueError('MPM grid is too fine for this version. Increase the spacing or reduce points per axis.')
    grid_m = lengths * 1e-3 / n
    particle_m = grid_m / settings.points_per_axis
    duration = settings.duration_ms * 1e-3
    young_pa = settings.young_mpa * 1e6
    nu = settings.poisson_ratio
    wave_speed = math.sqrt(young_pa * (1 - nu) / ((1 + nu) * (1 - 2*nu) * settings.density_kg_m3))
    # Both the wave transit time and the particle-to-platen spring bound the step.
    contact_omega = math.sqrt(young_pa / settings.density_kg_m3) / particle_m[2]
    step_limit = min(0.2 * grid_m.min() / wave_speed, 0.2 / contact_omega)
    steps = max(100, math.ceil(duration / step_limit))
    if steps > 50000:
        raise ValueError('MPM would require more than 50,000 time steps. Review the duration and grid spacing.')
    axes_mm = [(np.arange(count) + 0.5) * h * 1000 for count, h in zip(n_points, particle_m)]
    material = sample_box(geometry, axes_mm, threshold=geometry_threshold(geometry)) <= 0
    if not (material[:, :, 0].any() and material[:, :, -1].any()):
        raise ValueError('TPMS sampling missed a platen face. Refine the material-point spacing.')
    particles = np.argwhere(material)
    if len(particles) > 200000:
        raise ValueError('MPM sampled more than 200,000 points. Increase the particle spacing.')
    # Permit lateral expansion and grid crossing without box walls. Any escaped point fails the run.
    padding = np.maximum(2, np.ceil(n / 3).astype(int))
    padding[2] = 2
    axes = [np.arange(-pad, count + pad) for pad, count in zip(padding, n)]
    grid = np.stack(np.meshgrid(*axes, indexing='ij'), axis=-1).reshape(-1, 3)
    directory.mkdir(parents=True, exist_ok=True)
    _write_hex_model(directory / 'Body.mdpa', 'Parts_body', particles, particle_m, 0, material=True)
    _write_hex_model(directory / 'Grid.mdpa', 'Parts_grid', grid, grid_m, 0, material=False)
    law = dict(model_part_name='Initial_MPM_Material.Parts_body', properties_id=1, Material={
        'constitutive_law': {'name': 'HyperElasticNeoHookean3DLaw'},
        'Variables': dict(MATERIAL_POINTS_PER_ELEMENT=1, DENSITY=settings.density_kg_m3,
                          YOUNG_MODULUS=young_pa, POISSON_RATIO=nu, THICKNESS=1.0), 'Tables': {}})
    (directory / 'Materials.json').write_text(json.dumps({'properties': [law]}), encoding='ascii')
    case = dict(mpm=asdict(settings), geometry={key: getattr(geometry, key) for key in (
        'structure_name', 'tpms_morphology', 'size', 'cell_size', 'solid_fraction')},
        steps=steps, points=len(particles), grid_mm=grid_m[2] * 1000, point_mm=particle_m[2] * 1000,
        dimensions_mm=lengths.tolist(), padding_m=(padding * grid_m).tolist(),
        point_volume_m3=float(np.prod(particle_m)),
        sampled_fraction=len(particles) / int(np.prod(n_points)), dt_s=duration / steps)
    (directory / 'mpm_input.json').write_text(json.dumps(case, indent=2), encoding='utf-8')
    return case


def execute_mpm(geometry, settings, directory, *, remote=False, on_progress=None):
    from local_kratos import MPM_INSTALL_REQUIRED, local_mpm_python, system_dll_search

    if on_progress is None:
        from analysis_process import report_analysis_step
        on_progress = report_analysis_step
    python = sys.executable if remote else local_mpm_python()
    if python is None:
        raise RuntimeError(MPM_INSTALL_REQUIRED)
    directory = Path(directory).resolve()
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    driver = root / 'tools' / 'truba' / 'mpm_driver.py'
    on_progress('Sampling the TPMS and building the MPM background grid…')
    prepare_mpm_case(geometry, settings, directory)
    log_path = directory / 'mpm_solver.log'
    cores = os.environ.get('SLURM_CPUS_PER_TASK', '1') if remote else str(geometry.cores)
    environment = dict(os.environ, OMP_NUM_THREADS=cores)
    with system_dll_search(), log_path.open('w', encoding='utf-8') as log:
        with subprocess.Popen([str(python), '-I', '-X', 'utf8', '-X', 'faulthandler', '-u', str(driver)],
                              cwd=directory, env=environment,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8',
                              errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
            for line in process.stdout:
                log.write(line)
                if line.startswith('MPM progress: '):
                    log.flush()
                    on_progress(line.removeprefix('MPM progress: ').strip())
            exit_code = process.wait()
            if exit_code:
                log.flush()
                details = log_path.read_text(encoding='utf-8', errors='replace').splitlines()
                message = next((line for line in reversed(details) if line.startswith('RuntimeError:')), '')
                raise RuntimeError(f'Kratos MPM exited with code {exit_code}. {message} See {log_path}.')
    import pyvista as pv

    with np.load(directory / 'mpm_final_particles.npz') as data:
        cloud = pv.PolyData(data['coordinates_mm'])
        cloud.point_data['displacement_mm'] = data['displacement_mm']
        cloud.point_data['cauchy_stress_mpa'] = data['cauchy_stress_mpa']
        cloud.save(directory / PARTICLES_NAME)
    summary = json.loads((directory / 'mpm_run.json').read_text(encoding='utf-8'))
    summary['execution'] = 'TRUBA' if remote else 'Local'
    if remote:
        summary['slurm_job_id'] = os.environ.get('SLURM_JOB_ID')
    temporary = directory / 'mpm_summary.tmp'
    temporary.write_text(json.dumps(summary, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(directory / SUMMARY_NAME)
    summary, _ = read_mpm_result(directory)
    return summary


def run_local_mpm(geometry, settings, directory):
    execute_mpm(geometry, settings, directory)
    return str(directory)
