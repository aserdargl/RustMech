"""Compute dispatcher enforces CPU/RAM budgets and isolates cancellation."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import MagicMock, patch

from truba_session_worker import dispatch_pending, run_request


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / ('a' * 32)
        (self.root / 'inbox').mkdir(parents=True)

    def request(self, number, cores, memory):
        directory = self.base / f'{number:032x}'
        directory.mkdir()
        path = self.root / 'inbox' / f'{number:032x}.json'
        path.write_text(json.dumps(dict(directory=str(directory), cores=cores, memory_mb=memory)))
        return path, directory

    def test_parallel_admission_and_refill_respects_both_budgets(self):
        first, _ = self.request(1, 2, 40)
        second, _ = self.request(2, 2, 40)
        third, _ = self.request(3, 1, 30)
        release = {path: Event() for path in (first, second, third)}
        started = {path: Event() for path in release}
        def run(directory, cores, memory):
            path = self.root / 'inbox' / (directory.name + '.json')
            started[path].set()
            release[path].wait(5)
        active = {}
        with patch('truba_session_worker.run_request', side_effect=run), ThreadPoolExecutor(max_workers=4) as executor:
            try:
                dispatch_pending(self.root, active, executor, 4, 100)
                self.assertTrue(started[first].wait(2))
                self.assertTrue(started[second].wait(2))
                self.assertFalse(started[third].is_set())
                release[second].set()
                active[second][0].result(timeout=2)
                dispatch_pending(self.root, active, executor, 4, 100)
                self.assertTrue(started[third].wait(2))
                self.assertEqual(set(active), {first, third})
                self.assertTrue(second.with_suffix('.done').exists())
            finally:
                for item in release.values():
                    item.set()

    def test_ram_limit_blocks_step_even_with_spare_cores(self):
        first, _ = self.request(1, 1, 80)
        second, _ = self.request(2, 1, 40)
        release = Event()
        with patch('truba_session_worker.run_request', side_effect=lambda *a: release.wait(3)), ThreadPoolExecutor(max_workers=4) as executor:
            try:
                active = {}
                dispatch_pending(self.root, active, executor, 8, 100)
                self.assertEqual(set(active), {first})
                self.assertTrue(second.exists())
            finally:
                release.set()

    def test_invalid_resource_claim_fails_without_starting_solver(self):
        request, directory = self.request(1, 8, 20)
        with ThreadPoolExecutor(max_workers=1) as executor, patch('truba_session_worker.subprocess.Popen') as launch:
            dispatch_pending(self.root, {}, executor, 4, 100)
        launch.assert_not_called()
        self.assertEqual((directory / 'step.status').read_text(), 'FAILED|2:0')
        self.assertTrue(request.with_suffix('.done').exists())

    def test_srun_receives_exact_step_cpu_and_memory(self):
        _, directory = self.request(1, 2, 40)
        process = MagicMock(returncode=0)
        process.poll.return_value = 0
        with patch('truba_session_worker.subprocess.Popen', return_value=process) as launch:
            run_request(directory, 2, 40)
        command = launch.call_args.args[0]
        self.assertIn('--exclusive', command)
        self.assertIn('--exact', command)
        self.assertIn('--cpus-per-task=2', command)
        self.assertIn('--mem=40M', command)

    def test_launch_failure_is_recorded_without_stopping_other_steps(self):
        first, directory = self.request(1, 2, 40)
        second, _ = self.request(2, 2, 40)
        release = Event()
        def run(path, *args):
            if path == directory:
                raise OSError('srun unavailable')
            release.wait(3)
        with patch('truba_session_worker.run_request', side_effect=run), ThreadPoolExecutor(max_workers=2) as executor:
            try:
                active = {}
                dispatch_pending(self.root, active, executor, 4, 100)
                from concurrent.futures import wait
                wait([active[first][0]], timeout=2)
                dispatch_pending(self.root, active, executor, 4, 100)
                self.assertEqual(set(active), {second})
                self.assertEqual((directory / 'step.status').read_text(), 'FAILED|2:0')
                self.assertIn('srun unavailable', (directory / 'slurm.err').read_text())
            finally:
                release.set()


if __name__ == '__main__':
    unittest.main()
