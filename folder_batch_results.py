"""Durable folder batch metadata and portable per-specimen result exports."""
import csv
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, replace
from filecmp import cmp
from io import StringIO
import json
from pathlib import Path
import shutil
from uuid import uuid4


BATCH_DIRECTORY = 'BatchAnalysis'
LEGACY_BATCH_DIRECTORY = 'BatchAnaylsis'


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


def relocate_paths(value, aliases):
    if isinstance(value, dict):
        return {key: relocate_paths(item, aliases) for key, item in value.items()}
    if isinstance(value, list):
        return [relocate_paths(item, aliases) for item in value]
    if isinstance(value, str) and value:
        path = Path(value)
        for old, new in aliases:
            if path.is_relative_to(old):
                return str(new / path.relative_to(old))
    return value


def relocate_queue_row(row, aliases):
    settings = row.design_settings
    if settings is not None:
        settings = replace(settings, imported_geometry_path=relocate_paths(settings.imported_geometry_path, aliases))
    return replace(row, design_settings=settings, folder_job=relocate_paths(row.folder_job, aliases),
                   run_folder=relocate_paths(row.run_folder, aliases))


def relocate_batch_report(path, aliases, undo):
    if not path.is_file() or path.is_symlink():
        return
    try:
        with path.open(encoding='utf-8', newline='') as stream:
            original = stream.read()
    except UnicodeError:
        return
    if path.suffix == '.json':
        try:
            data = json.loads(original.lstrip('\ufeff'))
        except ValueError:
            return
        updated = relocate_paths(data, aliases)
        if updated == data:
            return
        content = ('\ufeff' if original.startswith('\ufeff') else '') + json.dumps(updated, ensure_ascii=False, indent=2)
    else:
        try:
            rows = list(csv.reader(StringIO(original, newline='')))
        except csv.Error:
            return
        updated = relocate_paths(rows, aliases)
        if updated == rows:
            return
        stream = StringIO(newline='')
        csv.writer(stream).writerows(updated)
        content = stream.getvalue()
    atomic_text(path, content)
    undo.callback(atomic_text, path, original)


def _plan_batch_move(source, target, moves, duplicates, emptied):
    if not target.exists() and not target.is_symlink() and not target.is_junction():
        moves.append((source, target))
    elif any(path.is_symlink() or path.is_junction() for path in (source, target)):
        raise OSError(f'Batch workspace contains a linked path: {target}')
    elif source.is_dir() and target.is_dir():
        for child in sorted(source.iterdir()):
            _plan_batch_move(child, target / child.name, moves, duplicates, emptied)
        emptied.append(source)
    elif source.is_file() and target.is_file() and cmp(source, target, shallow=False):
        duplicates.append(source)
    else:
        raise OSError(f'Batch workspace contains conflicting files: {target}')


@contextmanager
def move_batch_workspace(root):
    """Move the legacy workspace, rolling files back if the queue update fails."""
    root = Path(root).resolve()
    old, new = root / LEGACY_BATCH_DIRECTORY, batch_directory(root)
    if any(path.is_symlink() or path.is_junction() for path in (old, new)):
        raise OSError(f'Batch workspace contains a linked path: {root}')
    moves, duplicates, emptied = [], [], []
    if old.exists():
        if not old.is_dir():
            raise OSError(f'Expected a batch workspace directory: {old}')
        _plan_batch_move(old, new, moves, duplicates, emptied)
    elif not new.is_dir():
        yield None
        return

    aliases = [(old, new)]
    with ExitStack() as undo:
        for source, target in moves:
            source.rename(target)
            undo.callback(target.rename, source)
        results = new / 'Results'
        for name in ('job.json', 'summary.json', 'toplu_ozet.csv'):
            for path in results.rglob(name):
                if path.resolve().is_relative_to(new):
                    relocate_batch_report(path, aliases, undo)
        yield aliases[0]
        undo.pop_all()

    # These files have verified identical copies and the remaining directories
    # are empty. A cleanup failure leaves an intact, retryable legacy remnant.
    for path in duplicates:
        try:
            path.unlink()
        except OSError:
            pass
    for path in emptied:
        try:
            path.rmdir()
        except OSError:
            pass


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


def validate_folder_snapshot(settings):
    from model import validate_settings
    from solver_api import get_solver_capabilities
    errors = validate_settings(settings) + get_solver_capabilities(settings.solver_backend).validate(settings)
    if errors:
        raise ValueError(settings.imported_geometry_name + ': ' + '\n'.join(errors))


def run_remote_folder_doe(index, settings, context, solve, destination, staged_geometry=None, remote_auth=None,
                          job_marker=None):
    """Keep potentially large download copies off the GUI thread."""
    from types import SimpleNamespace
    from truba_remote import run_remote_doe
    payload = run_remote_doe(index, settings, context, solve, remote_auth, job_marker,
                             staged_geometry=staged_geometry)
    if solve and payload is not None:
        try:
            row = SimpleNamespace(folder_job={'destination': destination})
            payload = collect_folder_output(row, payload)
        except (OSError, ValueError) as error:
            payload = dict(payload, folder_export_error='Output copy failed; original retained: ' + str(error))
    return payload


run_remote_folder_doe.remote_job_target = True


def write_folder_reports(rows, *, summary_name='toplu_ozet.csv'):
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
    for results, jobs in grouped.items():
        stream = StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in jobs:
            writer.writerow(dict(source=row.folder_job['source'], solver=row.design_settings.solver_backend,
                                 direction=json.dumps(row.folder_job['direction']), status=row.status,
                                 max_force_n=row.max_force_n, effective_modulus_mpa=row.effective_modulus_mpa,
                                 energy_absorption_j=row.energy_absorption_j, run_folder=row.run_folder,
                                 job_id=row.exp_id))
        atomic_text(results / summary_name, '\ufeff' + stream.getvalue())


def folder_job_row(settings, metadata):
    from doe_studio import generate_doe_rows
    from model import settings_from_dict
    snapshot = settings_from_dict(asdict(settings))
    row = generate_doe_rows([snapshot], {}, "grid", 1, 42)[0]
    row.exp_id = "JOB-" + uuid4().hex[:10].upper()
    row.folder_job = dict(metadata)
    row.folder_job['destination'] = str(Path(metadata['destination']) / row.exp_id)
    return row


class FolderResultMixin:
    def _write_folder_reports(self, *, summary_name="toplu_ozet.csv"):
        try:
            write_folder_reports(self._rows, summary_name=summary_name)
        except (OSError, ValueError, TypeError) as error:
            self._report_persistence_problem('Folder results could not be saved: ' + str(error))

    def _apply_payload(self, payload):
        row = self._rows[payload['r_idx']]
        if row.folder_job:
            if not payload.get('run_folder') and self._active_mode == 'solve':
                payload = dict(payload, run_folder=row.folder_job['destination'])
            try:
                if payload.get('folder_export_error'):
                    row.folder_job['export_error'] = payload['folder_export_error']
                else:
                    payload = collect_folder_output(row, payload)
                    row.folder_job.pop('export_error', None)
            except (OSError, ValueError) as error:
                row.folder_job['export_error'] = 'Output copy failed; original retained: ' + str(error)
        super()._apply_payload(payload)
        if row.folder_job and row.folder_job.get('export_error'):
            self._report_persistence_problem(row.folder_job['export_error'])
