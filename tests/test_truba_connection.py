"""TRUBA authentication, local secrets, connection states and keyboard behavior."""
import os
import socket
from threading import Event, Thread
from pathlib import Path
import tempfile
from time import monotonic
import unittest
from unittest.mock import MagicMock, patch

import paramiko
from PySide6.QtCore import QItemSelectionModel, QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox

from truba_active_jobs import PENDING_REASON_COLUMN, QUEUE_SEPARATOR, display_start
from truba_client import TrubaSession
from truba_connection import TrubaConnectionDialog
from truba_credentials import CredentialStore
from truba_jobs import HOST, RemoteAuth, write_record
from truba_ssh import ssh_connection
from window_state import WindowStateStore


def queue_output(*rows):
    return '\n'.join(row.replace('|', QUEUE_SEPARATOR, 10) for row in rows)


SELECT_ROW = QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows


class TrubaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.credentials = CredentialStore(self.directory)
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.shutdown()
            dialog.close()
        self.app.processEvents()
        self.temp.cleanup()

    def dialog(self):
        dialog = TrubaConnectionDialog(credential_store=self.credentials)
        dialog.state_dir = self.directory
        self.dialogs.append(dialog)
        return dialog

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_encrypted_roundtrip_tamper_and_forget(self):
        self.credentials.save("different-user", "secret-şifre")
        raw = self.credentials.path.read_bytes()
        self.assertNotIn(b"secret", raw)
        self.assertNotIn(b"different-user", raw)
        self.assertEqual(CredentialStore(self.directory).load(), ("different-user", "secret-şifre"))
        self.credentials.path.write_bytes(raw[:-5] + b"wrong")
        with self.assertRaises(OSError):
            self.credentials.load()
        self.credentials.forget()
        self.assertIsNone(self.credentials.load())

    def test_open_and_enter_never_connect(self):
        dialog = self.dialog()
        dialog.show()
        self.app.processEvents()
        self.assertEqual(dialog.password.echoMode(), QLineEdit.EchoMode.Password)
        self.assertEqual(dialog.username.text(), "")
        with patch("truba_connection.ConnectionWorker") as worker:
            for field in (dialog.username, dialog.password):
                field.setFocus()
                QTest.keyClicks(field, "test")
                QTest.keyClick(field, Qt.Key.Key_Return)
            dialog.tabs.setCurrentWidget(dialog.active_jobs)
            dialog.active_jobs.study_name.setFocus()
            QTest.keyClicks(dialog.active_jobs.study_name, "A")
            QTest.keyClick(dialog.active_jobs.study_name, Qt.Key.Key_Return)
            self.app.processEvents()
            worker.assert_not_called()
        self.assertEqual(dialog.active_jobs.study_name.text(), "A")
        self.assertIsNone(dialog.worker)
        self.assertTrue(dialog.isVisible())

    def test_saved_credentials_and_opt_out(self):
        self.credentials.save("own-user", "own-password")
        dialog = self.dialog()
        self.assertEqual(dialog.username.text(), "own-user")
        self.assertEqual(dialog.password.text(), "own-password")
        self.assertTrue(dialog.remember.isChecked())
        self.assertIsNone(dialog.worker)
        dialog.remember.setChecked(False)
        self.assertIsNone(self.credentials.load())
        dialog.forget_password()
        self.assertEqual(dialog.password.text(), "")

    def test_queue_status_loads_only_on_visible_connected_tab(self):
        dialog = self.dialog()
        tab = dialog.queue_status
        auth = RemoteAuth('tester', 'test')
        with (patch.object(dialog, 'analysis_auth', return_value=None) as current_auth,
              patch('truba_queue_status.RemoteTransport') as transport):
            command = transport.return_value.command
            command.side_effect = [
                'TRUBA login banner\norfoz|up|96/16/0/112|1/0/0/1\nhamsi|drain|0/0/56/56|0/0/1/1',
                RuntimeError('Unavailable'),
            ]
            dialog.show()
            dialog.tabs.setCurrentWidget(tab)
            self.app.processEvents()
            self.assertFalse(tab.refresh_button.isEnabled())
            transport.assert_not_called()

            dialog.tabs.setCurrentWidget(dialog.settings_scroll)
            current_auth.return_value = auth
            dialog.connection_changed.emit(True)
            self.app.processEvents()
            transport.assert_not_called()
            dialog.tabs.setCurrentWidget(tab)
            self.assertTrue(tab.timer.isActive())
            self.assertTrue(tab.worker.wait(5000))
            self.app.processEvents()
            transport.assert_called_once()
            self.assertIn('sinfo ', command.call_args.args[0])
            self.assertIn('%R|%a|%C|%F', command.call_args.args[0])
            self.assertEqual(tab.table.rowCount(), 2)
            self.assertEqual([tab.table.item(0, i).text() for i in range(6)],
                             ['hamsi', 'Draining', '0 / 56 (0%)', '0', '56', '0 / 1'])
            self.assertEqual([tab.table.item(1, i).text() for i in range(6)],
                             ['orfoz', 'Accepting jobs', '96 / 112 (86%)', '16', '0', '0 / 1'])
            self.assertTrue(tab.updated_at)
            tab.refresh_button.click()
            self.assertTrue(tab.worker.wait(5000))
            self.app.processEvents()
            self.assertEqual(tab.table.rowCount(), 2)
            self.assertIn('Could not update queue status.', tab.notice.text())
            self.assertIn(tab.updated_at, tab.notice.text())

            dialog.tabs.setCurrentWidget(dialog.settings_scroll)
            self.assertFalse(tab.timer.isActive())
            current_auth.return_value = None
            dialog.connection_changed.emit(False)
            self.assertEqual(tab.table.rowCount(), 0)

    def test_button_states_and_save_only_after_authentication(self):
        dialog = self.dialog()
        dialog.username.setText("my-user")
        dialog.password.setText("my-password")
        dialog.remember.setChecked(True)
        dialog._state_changed("connecting")
        self.assertIsNone(self.credentials.load())
        dialog._state_changed("authentication_failed")
        self.assertIsNone(self.credentials.load())
        dialog._state_changed("connected")
        self.assertTrue(dialog.connected)
        self.assertEqual(dialog.connect_button.text(), "Connected")
        self.assertTrue(dialog.connect_button.property("trubaConnected"))
        self.assertEqual(self.credentials.load(), ("my-user", "my-password"))
        dialog._state_changed("reconnecting")
        self.assertFalse(dialog.connected)
        self.assertFalse(dialog.connect_button.property("trubaConnected"))

    def test_show_password_masks_on_hide_and_enter_does_not_connect(self):
        dialog = self.dialog()
        dialog.password.setText("test-only-password")
        dialog.show()
        dialog.show_password.setChecked(True)
        self.assertEqual(dialog.password.echoMode(), QLineEdit.EchoMode.Normal)
        with patch("truba_connection.ConnectionWorker") as worker:
            dialog.password.setFocus()
            QTest.keyClick(dialog.password, Qt.Key.Key_Return)
            worker.assert_not_called()
        dialog.hide()
        self.assertFalse(dialog.show_password.isChecked())
        self.assertEqual(dialog.password.echoMode(), QLineEdit.EchoMode.Password)
        self.assertEqual(dialog.password.text(), "test-only-password")

    def test_connection_failures_are_distinct_and_never_expose_exception_text(self):
        cases = (
            (socket.timeout("secret-from-server"), "connection_timeout"),
            (EOFError("secret-from-server"), "ssh_failed"),
            (paramiko.BadAuthenticationType("secret-from-server", ["publickey"]), "password_auth_unavailable"),
        )
        for error, expected in cases:
            with self.subTest(expected=expected):
                states, details = [], []
                session = TrubaSession(
                    "user", "password", self.directory, on_state=states.append,
                    on_host_key=self.fail, on_failure=details.append,
                )
                client = MagicMock()
                client.connect.side_effect = error
                with patch("truba_client.paramiko.SSHClient", return_value=client):
                    session.run()
                self.assertEqual(states, ["connecting", expected])
                self.assertEqual(details, [type(error).__name__] if expected == "ssh_failed" else [])
                self.assertEqual(session._password, "")
                self.assertIsNone(session.auth)
                client.close.assert_called_once()

    def test_rejected_password_never_falls_back_to_personal_keys_or_retries(self):
        states = []
        session = TrubaSession(
            "installer-user", "installer-password", self.directory,
            on_state=states.append, on_host_key=self.fail, on_failure=self.fail,
        )
        client = MagicMock()
        client.connect.side_effect = paramiko.AuthenticationException()
        with patch("truba_client.paramiko.SSHClient", return_value=client):
            session.run()
        kwargs = client.connect.call_args.kwargs
        self.assertEqual(kwargs["username"], "installer-user")
        self.assertEqual(kwargs["password"], "installer-password")
        self.assertFalse(kwargs["allow_agent"])
        self.assertFalse(kwargs["look_for_keys"])
        client.connect.assert_called_once()
        self.assertEqual(states, ["connecting", "authentication_failed"])
        self.assertEqual(session._password, "")
        client.close.assert_called_once()

    def test_authenticated_session_and_disconnect(self):
        states = []
        client = MagicMock()

        def changed(state):
            states.append(state)
            if state == "connected":
                self.assertEqual(session.auth, RemoteAuth("user", "password"))
                with ssh_connection(session.auth) as pooled:
                    self.assertIs(pooled, client)
                session.stop()

        session = TrubaSession(
            "user", "password", self.directory, on_state=changed,
            on_host_key=self.fail, on_failure=self.fail,
        )
        with patch("truba_client.paramiko.SSHClient", return_value=client) as create:
            session.run()
        create.assert_called_once()
        self.assertEqual(states, ["connecting", "connected"])
        client.get_transport.return_value.set_keepalive.assert_called_once_with(15)
        self.assertIsNone(session.auth)

    def test_unknown_host_requires_explicit_acceptance_and_changed_host_is_rejected(self):
        states = []
        session = TrubaSession(
            "user", "password", self.directory, on_state=states.append,
            on_host_key=lambda _: session.decide_host_key(False), on_failure=self.fail,
        )
        key = MagicMock()
        key.asbytes.return_value = b"server-key"
        client = MagicMock()
        with self.assertRaises(paramiko.SSHException):
            session.missing_host_key(client, "host", key)
        self.assertEqual(states, ["host_key_confirmation"])
        client.save_host_keys.assert_not_called()
        client.connect.side_effect = paramiko.BadHostKeyException("host", key, key)
        states.clear()
        with patch("truba_client.paramiko.SSHClient", return_value=client):
            session.run()
        self.assertEqual(states, ["connecting", "host_key_changed"])

    def test_stop_during_host_key_confirmation_does_not_trust_key(self):
        session = TrubaSession(
            "user", "password", self.directory, on_state=lambda _: None,
            on_host_key=lambda _: session.stop(), on_failure=self.fail,
        )
        client = MagicMock()
        key = MagicMock()
        key.asbytes.return_value = b"server-key"
        with self.assertRaises(paramiko.SSHException):
            session.missing_host_key(client, "host", key)
        client.save_host_keys.assert_not_called()

    def test_truba_window_geometry_restores(self):
        store = WindowStateStore(self.app, QSettings(str(self.directory / "windows.ini"), QSettings.Format.IniFormat))
        try:
            first = self.dialog()
            first.resize(640, 380)
            first.move(100, 110)
            first.show()
            self.app.processEvents()
            expected = first.frameGeometry()
            first.hide()
            second = self.dialog()
            second.show()
            self.app.processEvents()
            self.assertEqual(second.frameGeometry(), expected)
        finally:
            self.app.removeEventFilter(store)

    def test_jobs_splitter_resizes_table_and_restores_after_reopening(self):
        store = WindowStateStore(self.app, QSettings(str(self.directory / 'splitter.ini'), QSettings.Format.IniFormat))
        try:
            first = self.dialog()
            first.resize(680, 650)
            first.show()
            self.app.processEvents()
            splitter = first.jobs_splitter
            before = first.job_list.table.height()
            upper_before = first.tabs.height()
            handle = splitter.handle(1)
            center = handle.rect().center()
            QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=center)
            QTest.mouseMove(handle, center - QPoint(0, 150))
            QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=center)
            self.app.processEvents()
            self.assertGreater(first.job_list.table.height(), before)
            self.assertGreater(first.job_list.table.height(), 160)
            self.assertLess(first.tabs.height(), upper_before)
            smaller_upper = first.tabs.height()
            QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=center)
            QTest.mouseMove(handle, center + QPoint(0, 60))
            QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=center)
            self.app.processEvents()
            self.assertGreater(first.tabs.height(), smaller_upper)
            self.assertGreater(first.tabs.height(), 0)
            expected = splitter.sizes()
            first.hide()
            second = self.dialog()
            second.show()
            self.app.processEvents()
            self.assertEqual(second.jobs_splitter.sizes(), expected)
            self.assertEqual(second.size(), first.size())
        finally:
            self.app.removeEventFilter(store)

    def test_active_jobs_refresh_button_updates_slurm_queue(self):
        dialog = self.dialog()
        tab = dialog.active_jobs
        dialog.tabs.setCurrentWidget(tab)
        dialog.show()
        self.app.processEvents()
        self.assertFalse(tab.refresh_button.isEnabled())

        with (patch.object(dialog, 'analysis_auth', return_value=RemoteAuth('tester', 'test')),
              patch('truba_active_jobs.RemoteTransport') as transport):
            command = transport.return_value.command
            command.return_value = ''
            dialog.connection_changed.emit(True)
            self.assertTrue(tab.worker.wait(5000))
            self.app.processEvents()
            self.assertTrue(tab.refresh_button.isEnabled())

            tab.study_name.setFocus()
            QTest.keyClick(tab.study_name, Qt.Key.Key_Return)
            command.assert_called_once()

            command.return_value = queue_output('1|PENDING|N/A|0:00|8:00:00|8|1|short|Resources|New job|')
            QTest.mouseClick(tab.refresh_button, Qt.MouseButton.LeftButton)
            self.assertFalse(tab.refresh_button.isEnabled())
            tab.refresh_button.click()
            self.assertTrue(tab.worker.wait(5000))
            self.app.processEvents()
            self.assertEqual(command.call_count, 2)
            self.assertEqual(tab.jobs.item(0, 0).text(), '1')
            self.assertEqual(tab.jobs.item(0, PENDING_REASON_COLUMN).text(), 'Waiting for resources')
            self.assertTrue(tab.refresh_button.isEnabled())
            self.assertLess(tab.cancel_button.geometry().right(), tab.refresh_button.x())
            self.assertEqual(tab.cancel_button.y(), tab.refresh_button.y())
            self.assertEqual(tab.cancel_button.size(), tab.refresh_button.size())
            tab.layout().activate()
            bottom_margin = tab.layout().contentsMargins().bottom()
            self.assertEqual(tab.splitter.geometry().bottom(), tab.rect().bottom() - bottom_margin)

            command.return_value = queue_output('1|PENDING|N/A|0:00|8:00:00|8|1|short|Priority|New job|')
            tab.timer.timeout.emit()
            self.assertTrue(tab.worker.wait(5000))
            self.app.processEvents()
            self.assertEqual(tab.jobs.item(0, PENDING_REASON_COLUMN).text(), 'Higher-priority jobs')

        dialog.connection_changed.emit(False)
        self.assertFalse(tab.refresh_button.isEnabled())

    def test_active_jobs_cancels_only_confirmed_selection_and_refreshes(self):
        dialog = self.dialog()
        tab = dialog.active_jobs
        dialog.tabs.setCurrentWidget(tab)
        dialog.show()
        self.assertFalse(tab.cancel_button.isEnabled())
        queue = queue_output(
            '11|RUNNING|N/A|4:00|8:00:00|8|1|short|None|First job|run.slurm',
            '23|PENDING|N/A|0:00|8:00:00|4|1|short|Resources|Second job|run.slurm',
            '42_5|PENDING|N/A|0:00|8:00:00|8|1|short|None|Array task|run.slurm',
        )
        with (patch.object(dialog, 'analysis_auth', return_value=RemoteAuth('tester', 'test')),
              patch('truba_active_jobs.RemoteTransport') as transport,
              patch.object(QMessageBox, 'question') as question):
            command = transport.return_value.command
            command.return_value = queue
            dialog.connection_changed.emit(True)
            self.assertTrue(tab.worker.wait(5000))
            self.app.processEvents()
            tab.timer.stop()

            tab.jobs.setCurrentCell(2, 0, SELECT_ROW)
            self.assertFalse(tab.cancel_button.isEnabled())
            tab.jobs.selectionModel().select(tab.jobs.model().index(0, 0),
                                             QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
            self.assertFalse(tab.cancel_button.isEnabled())
            tab.jobs.setCurrentCell(1, 0, SELECT_ROW)
            self.assertTrue(tab.cancel_button.isEnabled())
            tab.study_name.setFocus()
            QTest.keyClick(tab.study_name, Qt.Key.Key_Return)
            question.assert_not_called()
            question.return_value = QMessageBox.StandardButton.No
            tab.cancel_button.click()
            command.assert_called_once()
            self.assertIn('23', question.call_args.args[2])
            self.assertEqual(question.call_args.args[-1], QMessageBox.StandardButton.No)

            def confirm(*_):
                tab.jobs.setCurrentCell(0, 0, SELECT_ROW)
                return QMessageBox.StandardButton.Yes

            question.side_effect = confirm
            command.side_effect = ['', queue.splitlines()[0]]
            tab.cancel_button.click()
            self.assertFalse(tab.cancel_button.isEnabled())
            self.assertTrue(tab.cancel_worker.wait(5000))
            self.app.processEvents()
            self.assertTrue(tab.worker.wait(5000))
            self.app.processEvents()
            self.assertEqual(command.call_args_list[1].args[0], 'scancel --user=tester 23')
            self.assertTrue(command.call_args_list[2].args[0].startswith('TZ=UTC'))
            self.assertEqual(tab.jobs.rowCount(), 1)
            self.assertEqual(tab.cancel_notice.text(), 'Cancellation requested for Slurm job 23.')

            command.side_effect = [RuntimeError('Rejected'), queue.splitlines()[0]]
            tab.cancel_button.click()
            self.assertTrue(tab.cancel_worker.wait(5000))
            self.app.processEvents()
            self.assertTrue(tab.worker.wait(5000))
            self.app.processEvents()
            self.assertTrue(tab.cancel_button.isEnabled())
            self.assertEqual(tab.selected_job_ids(), ('11',))
            self.assertIn('Could not request cancellation', tab.cancel_notice.text())

        dialog.connection_changed.emit(False)
        self.assertFalse(tab.cancel_button.isEnabled())

    def test_active_jobs_bulk_cancel_preserves_confirmed_ids_across_refresh(self):
        dialog = self.dialog()
        tab = dialog.active_jobs
        dialog.tabs.setCurrentWidget(tab)
        dialog.show()
        queue = queue_output(
            '11|RUNNING|N/A|4:00|8:00:00|8|1|short|None|First job|run.slurm',
            '23|PENDING|N/A|0:00|8:00:00|4|1|short|Resources|Second job|run.slurm',
            '31|RUNNING|N/A|4:00|8:00:00|8|1|short|None|Third job|run.slurm',
        ).splitlines()
        with (patch.object(dialog, 'analysis_auth', return_value=RemoteAuth('tester', 'test')),
              patch('truba_active_jobs.RemoteTransport') as transport,
              patch.object(QMessageBox, 'question') as question):
            command = transport.return_value.command
            command.return_value = '\n'.join(queue)
            dialog.connection_changed.emit(True)
            self.assertTrue(tab.worker.wait(5000))
            self.app.processEvents()
            tab.timer.stop()

            third_job = tab.jobs.visualItemRect(tab.jobs.item(2, 0)).center()
            QTest.mouseClick(tab.jobs.viewport(), Qt.MouseButton.LeftButton,
                             Qt.KeyboardModifier.ShiftModifier, third_job)
            self.assertEqual(tab.selected_job_ids(), ('11', '23', '31'))
            tab.jobs.setCurrentCell(0, 0, SELECT_ROW)
            QTest.mouseClick(tab.jobs.viewport(), Qt.MouseButton.LeftButton,
                             Qt.KeyboardModifier.ControlModifier, third_job)
            self.assertEqual(tab.selected_job_ids(), ('11', '31'))

            original_groups = tab.groups
            tab.show_groups(dict(reversed(tuple(original_groups.items()))))
            self.assertEqual(tab.selected_job_ids(), ('31', '11'))

            question.return_value = QMessageBox.StandardButton.No
            tab.cancel_button.click()
            command.assert_called_once()
            self.assertIn('2 selected Slurm jobs', question.call_args.args[2])
            self.assertEqual(question.call_args.args[-1], QMessageBox.StandardButton.No)

            def confirm_stale_selection(*_):
                tab.show_groups({'Third job': original_groups['Third job']})
                return QMessageBox.StandardButton.Yes

            question.side_effect = confirm_stale_selection
            tab.cancel_button.click()
            command.assert_called_once()
            self.assertIn('selected jobs changed', tab.cancel_notice.text())

            tab.show_groups(original_groups)
            tab.jobs.setCurrentCell(0, 0, SELECT_ROW)
            self.app.processEvents()
            tab.jobs.scrollToItem(tab.jobs.item(2, 0))
            third_job = tab.jobs.visualItemRect(tab.jobs.item(2, 0)).center()
            QTest.mouseClick(tab.jobs.viewport(), Qt.MouseButton.LeftButton,
                             Qt.KeyboardModifier.ControlModifier, third_job)
            self.assertEqual(tab.selected_job_ids(), ('11', '31'))

            def confirm(*_):
                tab.jobs.setCurrentCell(1, 0, SELECT_ROW)
                return QMessageBox.StandardButton.Yes

            question.side_effect = confirm
            command.side_effect = ['', queue[1]]
            tab.cancel_button.click()
            self.assertFalse(tab.cancel_button.isEnabled())
            self.assertTrue(tab.cancel_worker.wait(5000))
            self.app.processEvents()
            self.assertTrue(tab.worker.wait(5000))
            self.app.processEvents()
            self.assertEqual(command.call_args_list[1].args[0], 'scancel --user=tester 11 31')
            self.assertEqual(tab.selected_job_ids(), ('23',))
            self.assertEqual(tab.cancel_notice.text(), 'Cancellation requested for 2 Slurm jobs.')

    def test_active_jobs_show_purposes_resources_and_times_in_numbered_rows(self):
        dialog = self.dialog()
        tab = dialog.active_jobs
        dialog.state_dir = self.directory / 'TRUBA'
        for index, (job_id, username, name, operation, geometry) in enumerate([
            ('1', 'tester', 'A', 'mechanical', 'Gyroid'),
            ('2', 'tester', 'A', 'thermal_solve', ''),
            ('3', 'other', 'Private', 'flow_solve', ''),
            ('6', 'tester', 'A', 'doe_mesh', ''),
            ('6', 'tester', 'A', 'doe_solve', ''),
            ('6', 'tester', 'B', 'fft_solve', ''),
            ('9', 'tester', '', 'preview', ''),
        ]):
            write_record(dialog.state_dir / 'jobs' / f'{index}.json',
                         dict(host=HOST, username=username, job_id=job_id, state='RUNNING',
                              study_name=name, operation=operation, geometry_name=geometry))
        write_record(dialog.state_dir / 'resource_session.json', dict(
            host=HOST, username='tester', job_id='6', state='RUNNING', kind='rustmech_session'))
        esen_id = 'a' * 32
        write_record(self.directory / 'ESEN' / 'jobs' / esen_id / 'job.json',
                     dict(username='tester', job_id='8', name='run_material.slurm'))
        output = queue_output(
            '1|RUNNING|2026-11-18T04:15:00|1-05:20:40|2:17:15|64|2|orfoz|None|RM-1|/scratch/a|b/solve.slurm',
            '2|PENDING|2026-11-18T23:15:00|0:00|8:00:00|32|1|orfoz|Priority|RM-2|/scratch/thermal.slurm',
            '3|RUNNING|2026-11-18T05:00:00|25:40|UNLIMITED|20|1|short|None|External job|bash',
            '4|COMPLETED|2026-11-18T04:15:00|1:30|0:00|20|1|short|None|Done|bash',
            '5_[1-16%4]|PENDING|N/A|0:00|8:00:00|4|1|short|JobArrayTaskLimit|Array job|array.slurm',
            '6|RUNNING|2026-11-18T04:15:00|25:40|8:00:00|32|1|short|None|RustMech-session|session.slurm',
            '7|RUNNING|2026-11-18T04:15:00|25:40|8:00:00|20|1|short|None|bash|',
            f'8|RUNNING|2026-11-18T04:15:00|25:40|8:00:00|16|1|short|None|ESEN-{esen_id}|/scratch/run.slurm',
            '9|RUNNING|2026-11-18T04:15:00|25:40|8:00:00|8|1|short|None|RustMech-remote|preview.slurm',
        )
        with (patch.object(dialog, 'analysis_auth', return_value=RemoteAuth('tester', 'test')),
              patch('truba_active_jobs.esen_jobs_directory', return_value=self.directory / 'ESEN' / 'jobs'),
              patch('truba_active_jobs.RemoteTransport') as transport):
            transport.return_value.command.return_value = output
            dialog.show()
            self.app.processEvents()
            transport.assert_not_called()
            dialog.tabs.setCurrentWidget(tab)
            self.app.processEvents()
            if tab.worker is not None:
                tab.worker.wait()
            self.app.processEvents()
            transport.assert_called_once()
            command = transport.return_value.command.call_args.args[0]
            self.assertIn('--user=tester', command)
            self.assertIn('--all', command.split())
            self.assertIn(QUEUE_SEPARATOR.join(('%i', '%T', '%S', '%M', '%L', '%C', '%D', '%P', '%r', '%j', '%o')),
                          command)
            self.assertEqual([tab.works.item(i, 0).text() for i in range(tab.works.rowCount())],
                             ['All jobs', 'A', 'Array job', 'B', 'bash', f'ESEN-{esen_id}', 'External job',
                              'RustMech-remote'])
            self.assertEqual(tab.works.currentRow(), 0)
            estimates = {tab.jobs.item(i, 0).text(): tab.jobs.item(i, 11).text()
                         for i in range(tab.jobs.rowCount())}
            self.assertEqual(estimates['1'], '—')
            self.assertEqual(estimates['2'], display_start('2026-11-18T23:15:00'))
            self.assertEqual(estimates['5_[1-16%4]'], '—')
            self.assertEqual(tab.jobs.horizontalHeader().logicalIndex(2), PENDING_REASON_COLUMN)
            reasons = {tab.jobs.item(i, 0).text(): tab.jobs.item(i, PENDING_REASON_COLUMN)
                       for i in range(tab.jobs.rowCount())}
            self.assertEqual(reasons['1'].text(), '—')
            self.assertEqual(reasons['2'].text(), 'Higher-priority jobs')
            self.assertEqual(reasons['2'].toolTip(), '<qt>Priority</qt>')
            self.assertEqual(reasons['5_[1-16%4]'].text(), 'JobArrayTaskLimit')
            self.assertFalse(tab.jobs.isColumnHidden(2))
            self.assertEqual({(tab.jobs.item(i, 0).text(), tab.jobs.item(i, 2).text()):
                              (tab.jobs.item(i, 1).text(), tab.jobs.item(i, 3).text())
                              for i in range(tab.jobs.rowCount())},
                             {('1', 'A'): ('RUNNING', 'Mechanical analysis · Gyroid'),
                              ('2', 'A'): ('PENDING', 'Thermal analysis'),
                              ('3', 'External job'): ('RUNNING', 'External job'),
                              ('5_[1-16%4]', 'Array job'): ('PENDING', 'Array job'),
                              ('6', 'A'): ('RUNNING', 'DOE mesh generation / DOE analysis'),
                              ('6', 'B'): ('RUNNING', 'FFT Homogenization'),
                              ('7', 'bash'): ('RUNNING', 'bash'),
                              ('8', f'ESEN-{esen_id}'): ('RUNNING', 'LAMMPS · run_material.slurm'),
                              ('9', 'RustMech-remote'): ('RUNNING', 'Mesh preview')})
            tab.jobs.setCurrentItem(tab.jobs.findItems('FFT Homogenization', Qt.MatchFlag.MatchExactly)[0])
            tab.show_groups(tab.groups)
            self.assertEqual(tab.jobs.item(tab.jobs.currentRow(), 2).text(), 'B')
            tab.works.setCurrentCell(1, 0)
            self.assertTrue(tab.jobs.isColumnHidden(2))
            self.assertFalse(tab.jobs.isColumnHidden(3))
            self.assertEqual([tab.jobs.item(i, 0).text() for i in range(tab.jobs.rowCount())], ['1', '2', '6'])
            self.assertEqual(tab.jobs.item(2, 3).toolTip(), '<qt>DOE mesh generation<br>DOE analysis</qt>')
            self.assertTrue(tab.jobs.verticalHeader().isVisible())
            model = tab.jobs.model()
            self.assertEqual([model.headerData(row, Qt.Orientation.Vertical) for row in range(tab.jobs.rowCount())],
                             [1, 2, 3])
            self.assertEqual([tab.jobs.item(0, column).text() for column in range(4, 8)],
                             ['solve.slurm', '64', '2', 'orfoz'])
            self.assertEqual(tab.jobs.item(0, 4).toolTip(), '<qt>/scratch/a|b/solve.slurm</qt>')
            self.assertNotEqual(tab.jobs.item(0, 8).text(), '—')
            self.assertEqual(tab.jobs.item(0, 9).text(), '29:20:40')
            self.assertEqual(tab.jobs.item(0, 10).text(), '02:17:15')
            tab.jobs.setCurrentCell(1, 0)
            scrollbar = tab.jobs.horizontalScrollBar()
            self.assertGreater(scrollbar.maximum(), 0)
            scrollbar.setValue(scrollbar.maximum())
            scroll_position = scrollbar.value()
            tab.show_groups(tab.groups)
            self.assertEqual(tab.jobs.currentItem().text(), '2')
            self.assertEqual(scrollbar.value(), scroll_position)
            self.assertEqual(tab.jobs.item(1, 5).text(), '32')
            self.assertEqual([tab.jobs.item(1, column).text() for column in range(8, 11)], ['—'] * 3)
            tab.works.setCurrentCell(5, 0)
            self.assertEqual(tab.jobs.item(0, 4).text(), 'run_material.slurm')
            tab.works.setCurrentCell(6, 0)
            self.assertEqual(tab.jobs.item(0, 0).text(), '3')
            self.assertEqual(tab.jobs.item(0, 9).text(), '00:25:40')
            self.assertEqual(tab.jobs.item(0, 10).text(), 'Unlimited')
            dialog.connection_changed.emit(False)
            self.assertEqual(tab.works.rowCount(), 0)
            self.assertEqual(tab.jobs.rowCount(), 0)

    def test_active_jobs_columns_and_splitter_restore_after_reopening(self):
        settings_path = str(self.directory / 'active.ini')
        store = WindowStateStore(self.app, QSettings(settings_path, QSettings.Format.IniFormat))
        try:
            with patch.object(self.app, '_rustmech_window_state', store, create=True):
                first = self.dialog()
                first.resize(690, 650)
                tab = first.active_jobs
                first.tabs.setCurrentWidget(tab)
                first.show()
                self.app.processEvents()
                self.assertEqual([tab.jobs.horizontalHeaderItem(column).text() for column in (2, 3, 4)],
                                 ['Work', 'Job', 'Command'])
                self.assertEqual(tab.works.horizontalHeaderItem(0).text(), 'Work')

                header = tab.jobs.horizontalHeader()
                original_width = header.sectionSize(1)
                border = QPoint(header.sectionViewportPosition(1) + original_width - 1, header.height() // 2)
                QTest.mousePress(header.viewport(), Qt.MouseButton.LeftButton, pos=border)
                QTest.mouseMove(header.viewport(), border + QPoint(35, 0))
                QTest.mouseRelease(header.viewport(), Qt.MouseButton.LeftButton, pos=border + QPoint(35, 0))
                self.assertEqual(header.sectionSize(1), original_width + 35)
                tab.jobs.setColumnWidth(2, 147)

                queue = queue_output('23|PENDING|N/A|0:00|8:00:00|4|1|short|Resources|Second job|run.slurm')
                with (patch.object(first, 'analysis_auth', return_value=RemoteAuth('tester', 'test')),
                      patch('truba_active_jobs.RemoteTransport') as transport):
                    transport.return_value.command.return_value = queue
                    tab.refresh()
                    self.assertTrue(tab.worker.wait(5000))
                    self.app.processEvents()
                self.assertEqual(header.sectionSize(1), original_width + 35)
                tab.works.setCurrentCell(1, 0)
                self.assertTrue(tab.jobs.isColumnHidden(2))

                tab.splitter.moveSplitter(250, 1)
                expected = tab.splitter.sizes()
                first.hide()
                store.settings = QSettings(settings_path, QSettings.Format.IniFormat)
                second = self.dialog()
                second.show()
                second.tabs.setCurrentWidget(second.active_jobs)
                self.app.processEvents()
                self.assertEqual(second.active_jobs.splitter.sizes(), expected)
                self.assertEqual(second.active_jobs.jobs.columnWidth(1), original_width + 35)
                self.assertEqual(second.active_jobs.jobs.columnWidth(2), 147)
        finally:
            self.app.removeEventFilter(store)

    def test_resize_and_status_changes_keep_controls_anchored(self):
        dialog = self.dialog()
        dialog.show()
        self.app.processEvents()
        controls = (dialog.username, dialog.password, dialog.show_password,
                    dialog.remember, dialog.forget_button, dialog.status,
                    dialog.connect_button, dialog.disconnect_button)

        def positions():
            return [(widget.mapTo(dialog.settings_scroll.viewport(), QPoint()), widget.size())
                    for widget in controls]

        expected = positions()
        for size in ((1000, 760), (360, 240), (640, 500)):
            dialog.resize(*size)
            self.app.processEvents()
            self.assertEqual(positions(), expected)
        dialog._state_changed("host_key_confirmation")
        self.app.processEvents()
        self.assertEqual(positions(), expected)
        dialog.resize(360, 240)
        self.app.processEvents()
        self.assertGreater(dialog.settings_scroll.horizontalScrollBar().maximum(), 0)
        self.assertGreater(dialog.settings_scroll.verticalScrollBar().maximum(), 0)
        dialog.settings_scroll.ensureWidgetVisible(dialog.disconnect_button)
        self.app.processEvents()
        button = dialog.disconnect_button
        self.assertTrue(dialog.settings_scroll.viewport().rect().contains(
            button.mapTo(dialog.settings_scroll.viewport(), button.rect().center())))

    def test_server_information_uses_one_compact_row(self):
        dialog = self.dialog()
        dialog.show()
        self.app.processEvents()
        server = dialog.settings_layout.itemAt(0).widget()
        account = dialog.credentials_group
        self.assertLessEqual(server.height(), server.fontMetrics().lineSpacing() + 8)
        self.assertEqual(account.y() - server.geometry().bottom() - 1,
                         dialog.settings_layout.spacing())

    @unittest.skipIf(os.environ.get("RUSTMECH_OFFLINE") == "1", "Offline run: local SSH socket handshake")
    def test_real_local_ssh_password_handshake(self):
        """Exercise Paramiko end to end without contacting TRUBA or real accounts."""
        received = []
        server_done = Event()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(8)
        port = listener.getsockname()[1]
        key = paramiko.RSAKey.generate(2048)
        class Server(paramiko.ServerInterface):
            def get_allowed_auths(self, username):
                return "password"
            def check_auth_password(self, username, password):
                received.append((username, password))
                return paramiko.AUTH_SUCCESSFUL
        def serve():
            transport = None
            try:
                connection, _ = listener.accept()
                transport = paramiko.Transport(connection)
                transport.add_server_key(key)
                transport.start_server(server=Server())
                server_done.wait(15)
            finally:
                if transport is not None:
                    transport.close()
                listener.close()
        server = Thread(target=serve, daemon=True)
        server.start()
        real_client = paramiko.SSHClient
        class LocalClient(real_client):
            def connect(self, host, **kwargs):
                return super().connect("127.0.0.1", port=port, **kwargs)
        dialog = self.dialog()
        dialog.username.setText("local-test-user")
        dialog.password.setText("local-test-password")
        dialog.remember.setChecked(True)
        try:
            with patch("truba_client.paramiko.SSHClient", LocalClient), patch(
                    "truba_connection.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                dialog.start_connection()
                deadline = monotonic() + 8
                while monotonic() < deadline:
                    QTest.qWait(20)
                    if dialog.connected:
                        break
                self.assertTrue(dialog.connected, dialog.status.text())
                self.assertEqual(received, [("local-test-user", "local-test-password")])
                self.assertEqual(self.credentials.load(), received[0])
                self.assertTrue((self.directory / "known_hosts").exists())
                dialog.stop_connection()
                for _ in range(150):
                    QTest.qWait(20)
                    if dialog.worker is None:
                        break
                self.assertIsNone(dialog.worker)
                self.assertFalse(dialog.connected)
        finally:
            server_done.set()
            server.join(10)


if __name__ == "__main__":
    unittest.main()
