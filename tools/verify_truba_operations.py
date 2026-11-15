"""Run small real TRUBA jobs, retaining recovery records for every operation.

Uses the application's Windows-protected login, never command-line passwords.
Re-running with the same --directory retrieves existing jobs without resubmission.
"""
import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from model import Settings
from flow_analysis import FlowSettings
from thermal_analysis import ThermalSettings
from truba_jobs import get_auth, write_record
from truba_remote import RemoteContext, load_remote_preview, run_remote_job


def cases(directory):
    mechanical = Settings(size=1, cell_size=.5, solid_fraction=70, mesh_resolution=.25,
        solver_backend='kratos', material_model='linear_elastic', young_modulus=1000,
        compression=.002, substeps=2, minimum_substeps=2, maximum_substeps=200,
        self_contact_mode='disabled', bottom_platen_contact='frictionless',
        top_platen_contact='frictionless', cores=2)
    tet = replace(mechanical, cell_size=1, solid_fraction=40, mesh_type='TET4',
                  mesh_resolution=.3, local_mesh_size=.15, tetra_strategy='netgen')
    import pyvista as pv
    surface = directory / 'cube.vtp'
    if not surface.exists():
        pv.Box(bounds=(0, 1, 0, 1, 0, 1)).triangulate().save(surface)
    imported = replace(tet, imported_geometry_path=str(surface.resolve()),
                       imported_geometry_name='validation_cube.vtp', compression=.001)
    return {
        'surface': dict(operation='surface', settings=asdict(mechanical)),
        'preview': dict(operation='preview', settings=asdict(mechanical)),
        'tet4_preview': dict(operation='preview', settings=asdict(tet)),
        'tet10_preview': dict(operation='preview', settings=asdict(replace(tet, mesh_type='TET10'))),
        'imported_preview': dict(operation='preview', settings=asdict(imported)),
        'imported_mechanical': dict(operation='mechanical', settings=asdict(imported)),
        'mechanical': dict(operation='mechanical', settings=asdict(mechanical)),
        'convergence_level': dict(operation='convergence_level', settings=asdict(mechanical),
                                  max_elements=10000, selected=[0, 1, 2]),
        'doe_mesh': dict(operation='doe_mesh', settings=asdict(mechanical), row=0),
        'doe_solve': dict(operation='doe_solve', settings=asdict(mechanical), row=0),
        'flow_solve': dict(operation='flow_solve', settings=asdict(Settings()),
            flow=asdict(FlowSettings(cells_across=12, iterations=500, empty_pipe=True))),
        'thermal_solve': dict(operation='thermal_solve', settings=asdict(Settings()),
            flow=asdict(FlowSettings(cells_across=12, inlet_length_mm=1, outlet_length_mm=1)),
            thermal=asdict(ThermalSettings(duration_s=.2, time_step_s=.01, output_interval_s=.05))),
    }


def verify(name, result, directory, request):
    operation = request['operation']
    output = directory / 'output'
    if operation == 'preview':
        from model import settings_from_dict
        preview = load_remote_preview(output / 'preview', settings=settings_from_dict(request['settings']))
        return bool(preview.mesh.grid.n_cells and preview.surface.n_cells and not preview.mesh.solve_errors)
    if operation == 'surface':
        import pyvista as pv
        surface = pv.read(output / 'surface.vtp')
        return bool(surface.n_cells and surface.is_manifold)
    if operation == 'mechanical':
        summary = result['summary']
        passed = summary['status'] == 'completed'
        if name == 'imported_mechanical':
            passed = (passed and abs(summary['graphs']['final_force_N'] - 1.) < .03
                      and summary['reactions']['maximum_relative_force_imbalance'] < .01)
        return passed
    if operation == 'convergence_level':
        return result['level']['status'] == 'completed'
    if operation in ('doe_mesh', 'doe_solve'):
        return result['row']['status'] == ('Mesh Ready' if operation == 'doe_mesh' else 'Completed')
    if operation == 'flow_solve':
        return json.loads((output / 'flow_summary.json').read_text())['status'] == 'converged'
    if operation == 'thermal_solve':
        value = json.loads((output / 'thermal_summary.json').read_text())
        return value['status'] == 'completed' and value['history'][-1]['tpms_mean_C'] > 20
    if operation in ('probe', 'prepare'):
        return bool(result['capabilities']['mesh'])
    raise ValueError('No validation for this operation')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--only', action='append', help='Run only this named case (repeatable)')
    parser.add_argument('--prepare-runtime', action='store_true')
    parser.add_argument('--runtime-python', default=RemoteContext().runtime_python)
    parser.add_argument('--foam-module', default=RemoteContext().foam_module)
    args = parser.parse_args()
    auth = get_auth()
    if auth is None:
        parser.error('Connect in RustMech with Remember password enabled. Never put the password on the command line.')
    directory = (args.directory or ROOT / 'build' / ('truba-live-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    context = RemoteContext(cores=2, memory_mb=8192, walltime='00:10:00', transport='ssh',
                            runtime_python=args.runtime_python, foam_module=args.foam_module)
    context.validate()
    requests = cases(directory)
    requests = {'probe': dict(operation='probe'), **requests}
    if args.prepare_runtime:
        # An old probe describes the old environment. Keep its recovery record
        # and qualify the changed runtime with a new, separately named job.
        requests = {'prepare': dict(operation='prepare'),
                    'runtime_probe': dict(operation='probe'),
                    **{key: value for key, value in requests.items() if key != 'probe'}}
    selected = args.only or list(requests)
    unknown = set(selected) - set(requests)
    if unknown:
        parser.error('Unknown cases: ' + ', '.join(sorted(unknown)))
    report_path = directory / 'verification.json'
    report = json.loads(report_path.read_text()) if report_path.exists() else dict(live_truba_test=True, cases={})
    report.update(passed=False, context=asdict(context), selected=selected)
    write_record(report_path, report)
    print('Verification directory: ' + str(directory), flush=True)
    for name in selected:
        request = dict(requests[name])
        case = directory / name
        case.mkdir(exist_ok=True)
        record_path = case / 'job' / 'remote_job.json'
        try:
            record = json.loads(record_path.read_text()) if record_path.exists() else None
            if name == 'imported_mechanical' and record is None:
                metadata = directory / 'imported_preview/job/output/preview/mesh.json'
                request['mesh_source'] = json.loads(metadata.read_text())['remote_source']
            result, job_directory = run_remote_job(request, context, auth,
                marker=case / 'current.json', directory=case / 'job', resume_record=record,
                on_progress=lambda stage: print(name + ': ' + stage, flush=True))
            record = json.loads(record_path.read_text())
            report['cases'][name] = dict(passed=verify(name, result, job_directory, request),
                job_id=record['job_id'], hostname=result['hostname'], slurm_status=record['slurm_status'],
                result=result, record=str(record_path))
            print(name + ': ' + ('PASSED' if report['cases'][name]['passed'] else 'FAILED'), flush=True)
        except Exception as error:
            report['cases'][name] = dict(passed=False, error=str(error), record=str(record_path))
            write_record(report_path, report)
            print(name + ': FAILED: ' + str(error), flush=True)
            return 1
        write_record(report_path, report)
    report['passed'] = all(report['cases'][name]['passed'] for name in selected)
    write_record(report_path, report)
    print('Report: ' + str(report_path), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
