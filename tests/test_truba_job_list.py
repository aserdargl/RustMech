"""Saved-job discovery and explicit asynchronous cancellation."""
import tempfile
import unittest
import paramiko
from pathlib import Path
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from truba_connection import TrubaConnectionDialog
from truba_credentials import CredentialStore
from truba_jobs import HOST, RemoteAuth, write_record
from truba_job_list import unfinished_records, job_description


class JobListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.dialog = TrubaConnectionDialog(credential_store=CredentialStore(self.directory))
        self.dialog.state_dir = self.directory

    def tearDown(self):
        self.dialog.shutdown()
        self.dialog.close()
        self.app.processEvents()
        self.temp.cleanup()

    def record(self, name, **values):
        record = dict(host=HOST, username='tester', state='PENDING|Resources',
                      job_id=name, operation='mechanical', local_directory=str(self.directory))
        record.update(values)
        path = self.directory / 'jobs' / f'{name}.json'
        write_record(path, record)
        return path, record

    def test_all_unfinished_records_survive_local_mode_and_reopening(self):
        self.record('1')
        self.record('2', operation='preview', state='RUNNING')
        self.record('3', state='downloaded')
        self.record('4', state='CANCELLED by 123')
        self.record('5', state='interrupted; resume using this job record')
        (self.directory / 'jobs' / 'broken.json').write_text('{')
        write_record(self.directory / 'resource_session.json', dict(
            host=HOST, kind='rustmech_session', username='tester', state='PENDING', job_id='6'))
        self.assertEqual(len(unfinished_records(self.directory)), 4)
        with patch('truba_job_list.cancel_remote') as cancel:
            self.dialog.show()
            self.app.processEvents()
            self.assertEqual(self.dialog.job_list.table.rowCount(), 4)
            self.assertFalse(self.dialog.job_list.table.cellWidget(0, 2).isEnabled())
            self.assertIsNone(self.dialog.worker)
            self.dialog.hide()
            self.record('7', operation='thermal_solve')
            self.dialog.show()
            self.app.processEvents()
            self.assertEqual(self.dialog.job_list.table.rowCount(), 5)
            cancel.assert_not_called()

    def test_purpose_contains_geometry_and_doe_row(self):
        write_record(self.directory / 'request.json', dict(
            settings=dict(structure_name='Gyroid'), row=12))
        _, record = self.record('1', operation='doe_solve')
        self.assertEqual(job_description(record), 'DOE analysis · Gyroid · DOE row 12')

    def test_background_jobs_show_distinct_codes_and_track_connected_owner(self):
        first, running = self.record('1', state='RUNNING|None', request_id='a' * 32,
                                     background=True, submission_attempted=True)
        second, queued = self.record('2', request_id='b' * 32, background=True, submission_attempted=True)
        self.record('3', username='other', request_id='c' * 32, background=True, submission_attempted=True)
        panel = self.dialog.job_list
        panel.refresh()
        table = panel.table
        self.assertIn('RM-AAAAAAAAAAAA', table.item(0, 0).text())
        self.assertIn('RM-BBBBBBBBBBBB', table.item(1, 0).text())
        self.assertEqual(table.item(0, 1).text(), 'Analysis started and running')
        self.assertEqual(table.item(1, 1).text(), 'Analysis queued')

        def sync(record, auth):
            if record == queued:
                write_record(second, dict(record, state='downloaded'))
                return dict(run_folder='results'), self.directory

        updates = []
        panel.background_updated.connect(lambda *args: updates.append(args))
        with patch.object(self.dialog, 'analysis_auth', return_value=RemoteAuth('tester', 'test')), patch(
                'truba_job_list.sync_background_analysis', side_effect=sync) as run:
            self.dialog.connection_changed.emit(True)
            panel.sync_worker.wait()
            self.app.processEvents()
            self.assertEqual([call.args[0] for call in run.call_args_list], [running, queued])
        self.assertEqual(len(updates), 1)
        self.assertEqual(table.item(1, 1).text(), 'Analysis complete · results downloaded')
        self.assertIsNone(table.cellWidget(1, 2))

    def test_cancel_uses_selected_record_and_keeps_failed_job_retryable(self):
        path, record = self.record('1')
        auth = RemoteAuth('tester', 'test-password')
        panel = self.dialog.job_list
        with patch.object(self.dialog, 'analysis_auth', return_value=auth), patch(
                'truba_job_list.cancel_remote', side_effect=RuntimeError('private detail')) as cancel:
            panel.refresh()
            panel.table.cellWidget(0, 2).click()
            for _ in range(100):
                QTest.qWait(10)
                if not panel.workers:
                    break
            self.assertFalse(panel.workers)
            cancel.assert_called_once_with(path, auth)
            self.assertEqual(panel.table.rowCount(), 1)
            self.assertTrue(panel.table.cellWidget(0, 2).isEnabled())
            self.assertNotIn('private detail', panel.hint.text())
        with patch.object(self.dialog, 'analysis_auth', return_value=auth), patch(
                'truba_job_list.cancel_remote', side_effect=lambda p, a: write_record(p, dict(record, state='cancelled'))):
            panel.table.cellWidget(0, 2).click()
            for _ in range(100):
                QTest.qWait(10)
                if not panel.workers:
                    break
            self.assertEqual(panel.table.rowCount(), 0)

    def test_other_account_cannot_cancel_and_session_uses_release_flow(self):
        path, record = self.record('1', kind='rustmech_session')
        panel = self.dialog.job_list
        releases = []
        panel.release_requested.connect(lambda: releases.append(True))
        with patch.object(self.dialog, 'analysis_auth', return_value=RemoteAuth('someone-else', 'test')):
            panel.refresh()
            self.assertFalse(panel.table.cellWidget(0, 2).isEnabled())
            panel.cancel(path, record)
        self.assertEqual(releases, [])
        with patch.object(self.dialog, 'analysis_auth', return_value=RemoteAuth('tester', 'test')):
            panel.cancel(path, record)
        self.assertEqual(releases, [True])

    def test_authentication_failure_is_explained_without_exposing_details(self):
        from truba_job_list import CancelJobWorker
        path, _ = self.record('1')
        worker = CancelJobWorker(path, RemoteAuth('tester', 'test'), self.dialog)
        with patch('truba_job_list.cancel_remote', side_effect=paramiko.AuthenticationException('private detail')):
            worker.run()
        self.assertTrue(worker.failed)
        self.assertIn('authentication rejected', worker.failure_message)
        self.assertNotIn('private detail', worker.failure_message)

    def test_saved_credentials_allow_explicit_cancel_when_panel_is_disconnected(self):
        path, record = self.record('1')
        panel = self.dialog.job_list
        with patch.object(self.dialog.credentials, 'load', return_value=('tester', 'test')), patch(
                'truba_job_list.cancel_remote', side_effect=lambda p, a: write_record(p, dict(record, state='cancelled'))) as cancel:
            panel.refresh()
            self.assertTrue(panel.table.cellWidget(0, 2).isEnabled())
            cancel.assert_not_called()
            panel.table.cellWidget(0, 2).click()
            for _ in range(100):
                QTest.qWait(10)
                if not panel.workers:
                    break
            cancel.assert_called_once_with(path, RemoteAuth('tester', 'test'))
            self.assertEqual(panel.table.rowCount(), 0)


if __name__ == '__main__':
    unittest.main()
