"""Real DoE row lifecycle with controlled out-of-order remote futures."""
from concurrent.futures import Future
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from truba_remote import RemoteContext
from doe_studio import POLICY_STOP_AFTER
from tests import test_doe_run_control as helpers


class RemoteFuture(Future):
    def __init__(self):
        super().__init__()
        self.stopped = False
        self.stop_error = False

    def force_stop(self):
        if self.stop_error:
            raise RuntimeError('still running')
        self.stopped = True


class ParallelBatchTests(unittest.TestCase):
    setUpClass = classmethod(helpers.DoERunControlTests.setUpClass.__func__)
    setUp = helpers.DoERunControlTests.setUp
    tearDown = helpers.DoERunControlTests.tearDown
    studio = helpers.DoERunControlTests.studio

    def remote_studio(self, rows=4):
        studio = self.studio(rows=rows)
        context = RemoteContext(partition='hamsi', cores=56, parallel_jobs=2, session_id='321')
        studio.remote_controller = SimpleNamespace(requested=True, context=lambda: context,
                                                   require=lambda *a, **kw: (context, None))
        self.futures = []
        self.arguments = []
        def launch(target, index, *args, **kwargs):
            future = RemoteFuture()
            self.futures.append((index, future))
            self.arguments.append((target, args, kwargs))
            return future
        item = patch('truba_parallel.AnalysisProcess', side_effect=launch)
        item.start()
        self.addCleanup(item.stop)
        return studio

    def finish(self, position, status='Completed'):
        index, future = self.futures[position]
        future.set_result(dict(r_idx=index, status=status))

    def test_out_of_order_completion_refills_only_free_slot_and_saves_each_result(self):
        studio = self.remote_studio()
        studio.start_batch_solve()
        self.assertEqual([i for i, _ in self.futures], [0, 1])
        self.finish(1)
        studio._poll_worker()
        self.assertEqual([i for i, _ in self.futures], [0, 1, 2])
        self.assertEqual(studio.rows[1].status, 'Completed')
        self.assertEqual(set(studio._parallel_futures), {0, 2})
        self.finish(0)
        studio._poll_worker()
        self.finish(2)
        studio._poll_worker()
        self.finish(3)
        studio._poll_worker()
        self.assertIsNone(studio._active_mode)
        self.assertEqual(studio.rows_to_run(), [])
        self.assertTrue(studio._dataset_path.is_file())
        self.assertTrue(all(row.duration_s is not None for row in studio.rows))

    def test_retry_targets_only_failed_row(self):
        studio = self.remote_studio(rows=2)
        studio.max_retries = 1
        studio.start_batch_solve()
        self.finish(1, 'Failed: solver error')
        studio._poll_worker()
        self.assertEqual([i for i, _ in self.futures], [0, 1, 1])
        self.finish(0)
        self.finish(2)
        studio._poll_worker()
        self.assertIsNone(studio._active_mode)
        self.assertEqual([r.attempts for r in studio.rows], [0, 1])

    def test_stop_retains_unconfirmed_job_and_stops_all_other_jobs(self):
        studio = self.remote_studio(rows=4)
        studio.start_batch_solve()
        self.futures[0][1].stop_error = True
        studio.stop_active_queue()
        self.assertEqual(set(studio._parallel_futures), {0})
        self.assertIsNotNone(studio._active_future)
        self.assertTrue(self.futures[1][1].stopped)
        self.assertEqual(studio.rows[2].status, 'Pending')
        self.futures[0][1].stop_error = False
        studio.stop_active_queue()
        self.assertIsNone(studio._active_future)
        self.assertIsNone(studio._active_mode)

    def test_failure_policy_drains_active_jobs_and_keeps_unstarted_rows_pending(self):
        studio = self.remote_studio(rows=4)
        studio.failure_policy, studio.max_failures = POLICY_STOP_AFTER, 1
        studio.start_batch_solve()
        self.finish(1, 'Failed: solver error')
        studio._poll_worker()
        self.assertEqual(len(self.futures), 2)
        self.assertIsNotNone(studio._active_mode)
        self.finish(0)
        studio._poll_worker()
        self.assertIsNone(studio._active_mode)
        self.assertEqual([r.status for r in studio.rows[2:]], ['Pending', 'Pending'])

    def test_parallel_materials_keep_independent_snapshots(self):
        from dataclasses import replace
        from model import Settings
        studio = self.remote_studio(rows=2)
        studio.rows[0].design_settings = replace(Settings(), material_name='Steel', young_modulus=200000)
        studio.rows[1].design_settings = replace(Settings(), material_name='Aluminium', young_modulus=70000)
        studio.start_batch_solve()
        settings = [args[0] for _, args, _ in self.arguments]
        self.assertEqual([(s.material_name, s.young_modulus) for s in settings],
                         [('Steel', 200000), ('Aluminium', 70000)])
        studio.stop_active_queue()

    def test_folder_jobs_keep_background_output_copy_and_destination(self):
        from folder_batch_results import run_remote_folder_doe
        studio = self.remote_studio(rows=2)
        destination = str(self.temp.name) + '/folder-result'
        studio.rows[1].folder_job = dict(destination=destination)
        studio.start_batch_solve()
        target, args, _ = self.arguments[1]
        self.assertIs(target, run_remote_folder_doe)
        self.assertEqual(args[-2:], (destination, None))
        studio.stop_active_queue()


if __name__ == '__main__':
    unittest.main()
