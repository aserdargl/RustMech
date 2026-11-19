"""Durable folder batch metadata and portable per-specimen result exports."""
import csv
from dataclasses import asdict
import json
from pathlib import Path
import shutil
from uuid import uuid4


BATCH_DIRECTORY = 'BatchAnaylsis'


def batch_directory(root):
    return Path(root) / BATCH_DIRECTORY


def atomic_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        temporary.write_text(content, encoding='utf-8', newline='')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def collect_folder_output(row, payload):
    """Remote downloads retain their original copy until the export succeeds."""
    destination = Path(row.folder_job['destination'])
    source = payload.get('run_folder')
    if source:
        source = Path(source).resolve()
        if not source.is_dir():
            raise FileNotFoundError('Result folder is unavailable: ' + str(source))
        if not source.is_relative_to(destination.resolve()):
            target = destination / ('Remote_' + uuid4().hex)
            shutil.copytree(source, target)
            # Existing remote reporting stores raw files beside HumanResults.
            raw = source.parent.parent / 'results' / source.name
            if source.parent.name == 'HumanResults' and raw.is_dir():
                shutil.copytree(raw, target / 'Raw')
            aliases = [(str(source), str(target)), (str(raw), str(target / 'Raw'))]
            def relocate(value):
                if isinstance(value, dict):
                    return {key: relocate(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [relocate(item) for item in value]
                if isinstance(value, str):
                    for before, after in aliases:
                        if value == before or value.startswith((before + '/', before + '\\')):
                            return after + value[len(before):]
                return value
            for report in target.rglob('summary.json'):
                atomic_text(report, json.dumps(relocate(json.loads(report.read_text(encoding='utf-8'))),
                                               ensure_ascii=False, indent=2))
            payload = dict(payload, run_folder=str(target))
    return payload


def run_remote_folder_doe(index, settings, context, solve, destination, remote_auth=None, job_marker=None):
    """Keep potentially large download copies off the GUI thread."""
    from types import SimpleNamespace
    from truba_remote import run_remote_doe
    payload = run_remote_doe(index, settings, context, solve, remote_auth, job_marker)
    if solve:
        try:
            row = SimpleNamespace(folder_job={'destination': destination})
            payload = collect_folder_output(row, payload)
        except (OSError, ValueError) as error:
            payload = dict(payload, folder_export_error='Output copy failed; original retained: ' + str(error))
    return payload


run_remote_folder_doe.remote_job_target = True


def write_folder_reports(rows):
    """Write an outcome (including failures) per job and a summary per input root."""
    grouped = {}
    for row in rows:
        if not row.folder_job:
            continue
        job = row.folder_job
        atomic_text(Path(job['destination']) / 'job.json',
                    json.dumps(asdict(row), ensure_ascii=False, allow_nan=False, indent=2))
        results = Path(job.get('results_root') or Path(job['root']) / 'Result')
        grouped.setdefault(results, []).append(row)
    columns = ('source', 'solver', 'direction', 'status', 'max_force_n',
               'effective_modulus_mpa', 'energy_absorption_j', 'run_folder', 'job_id')
    import io
    for results, jobs in grouped.items():
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in jobs:
            writer.writerow(dict(source=row.folder_job['source'], solver=row.design_settings.solver_backend,
                                 direction=json.dumps(row.folder_job['direction']), status=row.status,
                                 max_force_n=row.max_force_n, effective_modulus_mpa=row.effective_modulus_mpa,
                                 energy_absorption_j=row.energy_absorption_j, run_folder=row.run_folder,
                                 job_id=row.exp_id))
        atomic_text(results / 'toplu_ozet.csv', '\ufeff' + stream.getvalue())
