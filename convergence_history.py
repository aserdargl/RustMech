"""Recover submitted TRUBA mesh levels from the existing job registry."""

from dataclasses import dataclass
import base64
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from model import settings_from_dict
from truba_job_list import unfinished_records
from truba_jobs import SLURM_TERMINAL_STATES
from truba_remote import SlurmOutOfMemory, _rebase_paths, decode_convergence_results

FINISHED_MESH_STATES = SLURM_TERMINAL_STATES | {"DOWNLOADED"}


def mesh_job_state(record):
    states = [str(record.get(field) or "").upper().split("|")[0].split(" ")[0].rstrip("+")
              for field in ("state", "slurm_status", "step_status")]
    return next((state for state in states if state in FINISHED_MESH_STATES), states[0])


def mesh_job_finished(record):
    return mesh_job_state(record) in FINISHED_MESH_STATES


def mesh_job_needs_results(record):
    accounting = record.get("slurm_status") or record.get("step_status")
    return (accounting == "COMPLETED|0:0"
            and record.get("state") in ("downloading", "interrupted; resume using this job record"))


@dataclass
class SavedMeshJob:
    path: Path
    record: dict
    request: dict
    levels: list
    plan: dict
    modified: float

    @property
    def state(self):
        return mesh_job_state(self.record)

    @property
    def active(self):
        return not mesh_job_finished(self.record) or mesh_job_needs_results(self.record)

    def cached_results(self):
        directory = Path(self.record["local_directory"])
        if self.record.get("state") == "downloaded":
            try:
                result = json.loads((directory / "output" / "result.json").read_text(encoding="utf-8"))
                if (result.get("request_id") == self.record["request_id"]
                        and result.get("source_sha256") == self.record["source_sha256"]
                        and str(result.get("slurm_job_id")) == str(self.record["job_id"])):
                    decoded = decode_convergence_results(
                        _rebase_paths(directory, result, rewrite_summaries=False), directory,
                        self.request.get("convergence_steps"))
                    return decoded if isinstance(decoded, list) else [decoded]
            except (OSError, ValueError, KeyError, TypeError, RuntimeError, AttributeError):
                pass
        if self.state in SLURM_TERMINAL_STATES - {"COMPLETED"}:
            if self.state == "OUT_OF_MEMORY" and self.record.get("failure_memory_mb"):
                error = SlurmOutOfMemory(self.record["failure_memory_mb"], directory / "slurm.err")
            else:
                error = RuntimeError(f"Slurm · {self.state}")
            return [error] * len(self.levels)
        return None

    def saved_geometry(self):
        """Keep appended levels on the specimen that was actually uploaded."""
        directory = Path(self.record["local_directory"])
        path = directory / "study-geometry.vtp"
        if not path.is_file():
            request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
            encoded = request.get("imported_geometry_vtp")
            if not encoded:
                raise ValueError("The saved request does not contain the original specimen.")
            specimen = base64.b64decode(encoded, validate=True)
            stream = NamedTemporaryFile(dir=directory, suffix=".vtp.tmp", delete=False)
            staged = Path(stream.name)
            try:
                with stream:
                    stream.write(specimen)
                staged.replace(path)
            finally:
                staged.unlink(missing_ok=True)
        return path


def saved_mesh_studies(state_dir):
    studies = {}
    for path, record in unfinished_records(state_dir, include_analysis_history=True):
        if record.get("operation") != "convergence_level" or not record.get("job_id"):
            continue
        try:
            request_path = Path(record["local_directory"]) / "request.json"
            request = json.loads(request_path.read_text(encoding="utf-8"))
            request.pop("imported_geometry_vtp", None)
            steps = request.get("convergence_steps") or [request]
            levels = [settings_from_dict(step["settings"]) for step in steps]
            plan = record.get("convergence_study") or request.get("convergence_study") or {}
            key = (record["username"], plan.get("id") or f"legacy:{path.stem}")
            job = SavedMeshJob(path, record, request, levels, plan, request_path.stat().st_mtime)
            studies.setdefault(key, []).append(job)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    for jobs in studies.values():
        jobs.sort(key=lambda job: (job.plan.get("row", 0), job.modified))
    active_studies = ((key, jobs) for key, jobs in studies.items() if any(job.active for job in jobs))
    return dict(sorted(active_studies, key=lambda item: max(job.modified for job in item[1] if job.active),
                       reverse=True))
