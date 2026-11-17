"""Parallel remote batch lifecycle sharing the existing row/result persistence."""
from dataclasses import asdict, replace
from datetime import datetime
import time

from analysis_process import AnalysisProcess


class TrubaParallelMixin:
    def _parallel_limit(self):
        controller = getattr(self, 'remote_controller', None)
        if controller is None or not controller.requested or self._active_mode not in ('solve', 'check'):
            return 1
        context = controller.context()
        return min(context.parallel_jobs, context.cores, context.memory_mb)

    def _parallel_running(self):
        return bool(getattr(self, '_parallel_futures', {}))

    def _launch_next_in_queue(self):
        if not self._parallel_running() and (self._stop_requested or not self._queue or self._parallel_limit() <= 1):
            return self._launch_next_serial()
        active = self.__dict__.setdefault('_parallel_futures', {})
        limit = self._parallel_limit()
        for index in list(self._queue):
            if self._stop_requested or len(active) >= limit:
                break
            if index in active:
                continue
            row = self._rows[index]
            started = time.monotonic()
            row.started_at = datetime.now().isoformat(timespec='seconds')
            row.duration_s = None
            row.status = 'TRUBA · checking mesh' if self._active_mode == 'check' else 'TRUBA · solving'
            self._updating_table = True
            try:
                self._write_row_cells(index, row)
            finally:
                self._updating_table = False
            try:
                from truba_remote import run_remote_doe
                settings = row.to_settings(self._run_base())
                execution = self._run_base()
                if getattr(row, 'folder_job', None) and settings.solver_backend != execution.solver_backend:
                    raise ValueError('Folder job solver differs from the current TRUBA solver.')
                settings = replace(settings, **{key: value for key, value in asdict(execution).items()
                    if key.startswith('kratos_') or key in ('solver_backend', 'cores', 'automatic_cores')})
                context, auth = self.remote_controller.require('mesh' if self._active_mode == 'check' else settings.solver_backend, settings, allow_handover=True)
                if getattr(row, 'folder_job', None):
                    from folder_batch_results import run_remote_folder_doe
                    future = AnalysisProcess(run_remote_folder_doe, index, settings, context,
                        self._active_mode != 'check', row.folder_job['destination'], remote_auth=auth)
                else:
                    future = AnalysisProcess(run_remote_doe, index, settings, context,
                                             self._active_mode != 'check', remote_auth=auth)
                active[index] = (future, started)
            except Exception as error:
                self._parallel_apply(dict(r_idx=index, status='Failed to start: ' + str(error)), started)
        self._active_future = next((item[0] for item in active.values()), None)
        # Persist RUNNING state before returning to the event loop. Restarting
        # the app will therefore flag unfinished rows rather than duplicate them.
        if hasattr(self, 'save_queue'):
            self.save_queue()
        if not active:
            if self._queue and not self._stop_requested:
                # Retry on the next timer tick, without recursive start failures.
                self._parallel_retry_pending = True
            else:
                self._launch_next_serial()

    def _parallel_apply(self, payload, started):
        index = payload['r_idx']
        # The established row lifecycle expects its result at queue[0]. Move
        # just this entry so completion order cannot remove another active job.
        if index in self._queue:
            self._queue.remove(index)
            self._queue.insert(0, index)
        self._row_started = started
        self._apply_payload(payload)
        if self._stop_requested:
            self._queue.clear()

    def _poll_worker(self):
        active = getattr(self, '_parallel_futures', {})
        if not active:
            if getattr(self, '_parallel_retry_pending', False):
                self._parallel_retry_pending = False
                return self._launch_next_in_queue()
            return self._poll_serial()
        from doe_studio import TIMEOUT_STATUS
        for index, (future, started) in list(active.items()):
            timed_out = self.row_timeout_s > 0 and time.monotonic() - started > self.row_timeout_s
            if not future.done() and not timed_out:
                continue
            if not future.done():
                try:
                    future.force_stop()
                except Exception as error:
                    self._stop_requested = True
                    self._report_persistence_problem('Timed-out row could not be stopped: ' + str(error))
                    continue  # Keep ownership until cancellation is confirmed.
                payload = dict(r_idx=index, status=TIMEOUT_STATUS)
            else:
                try:
                    payload = future.result()
                except Exception as error:
                    payload = dict(r_idx=index, status='Failed: ' + str(error))
            del active[index]
            self._parallel_apply(payload, started)
        self._active_future = next((item[0] for item in active.values()), None)
        self._launch_next_in_queue()

    def stop_active_queue(self):
        active = getattr(self, '_parallel_futures', {})
        self._parallel_retry_pending = False
        if not active:
            return self._stop_serial()
        self._stop_requested = True
        for index, (future, started) in list(active.items()):
            try:
                future.force_stop()
            except Exception as error:
                self._report_persistence_problem('Forced Stop failed: ' + str(error))
                continue
            del active[index]
            # An explicit Stop never enters the ordinary failure/retry policy.
            row = self._rows[index]
            row.status = 'Cancelled (Forced Stop)'
            row.duration_s = round(time.monotonic() - started, 3)
            self._updating_table = True
            try:
                self._write_row_cells(index, row)
            finally:
                self._updating_table = False
        self._queue.clear()
        self._active_future = next((item[0] for item in active.values()), None)
        if self._active_mode == 'solve':
            self._persist_progress()
        if hasattr(self, 'save_queue'):
            self.save_queue()
        if not active:
            self._launch_next_serial()

    def _operation_state(self):
        active = getattr(self, '_parallel_futures', {})
        if not active:
            return self._serial_operation_state()
        message = f'TRUBA · {len(active)} analyses active · {len([i for i in self._queue if i not in active])} queued'
        return True, message, self._total_queued - len(self._queue), self._total_queued
