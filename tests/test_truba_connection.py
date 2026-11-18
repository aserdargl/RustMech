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
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox

from truba_connection import ConnectionWorker, TrubaConnectionDialog
from truba_credentials import CredentialStore
from truba_jobs import HOST, RemoteAuth, write_record
from window_state import WindowStateStore


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
                worker = ConnectionWorker("user", "password", self.directory)
                states, details = [], []
                worker.state_changed.connect(states.append)
                worker.failure_detail.connect(details.append)
                client = MagicMock()
                client.connect.side_effect = error
                with patch("truba_connection.paramiko.SSHClient", return_value=client):
                    worker.run()
                self.assertEqual(states, ["connecting", expected])
                self.assertNotIn("secret-from-server", str(details))
                self.assertEqual(worker.password, "")
                client.close.assert_called_once()

    def test_rejected_password_never_falls_back_to_personal_keys_or_retries(self):
        worker = ConnectionWorker("installer-user", "installer-password", self.directory)
        states = []
        worker.state_changed.connect(states.append)
        client = MagicMock()
        client.connect.side_effect = paramiko.AuthenticationException()
        with patch("truba_connection.paramiko.SSHClient", return_value=client):
            worker.run()
        kwargs = client.connect.call_args.kwargs
        self.assertEqual(kwargs["username"], "installer-user")
        self.assertEqual(kwargs["password"], "installer-password")
        self.assertFalse(kwargs["allow_agent"])
        self.assertFalse(kwargs["look_for_keys"])
        client.connect.assert_called_once()
        self.assertEqual(states, ["connecting", "authentication_failed"])
        self.assertEqual(worker.password, "")
        client.close.assert_called_once()

    def test_authenticated_session_and_disconnect(self):
        worker = ConnectionWorker("user", "password", self.directory)
        states = []
        def changed(state):
            states.append(state)
            if state == "connected":
                worker.stop()
        worker.state_changed.connect(changed)
        client = MagicMock()
        with patch("truba_connection.paramiko.SSHClient", return_value=client):
            worker.run()
        self.assertEqual(states, ["connecting", "connected"])
        client.get_transport.return_value.set_keepalive.assert_called_once_with(15)
        self.assertTrue(worker.stop_requested.is_set())

    def test_unknown_host_requires_explicit_acceptance_and_changed_host_is_rejected(self):
        worker = ConnectionWorker("user", "password", self.directory)
        worker.host_key_required.connect(lambda _: worker.host_decided.set())
        key = MagicMock()
        key.asbytes.return_value = b"server-key"
        client = MagicMock()
        with self.assertRaises(paramiko.SSHException):
            worker.missing_host_key(client, "host", key)
        client.save_host_keys.assert_not_called()
        client.connect.side_effect = paramiko.BadHostKeyException("host", key, key)
        states = []
        worker.state_changed.connect(states.append)
        with patch("truba_connection.paramiko.SSHClient", return_value=client):
            worker.run()
        self.assertEqual(states, ["connecting", "host_key_changed"])

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
            splitter.moveSplitter(180, 1)
            self.app.processEvents()
            self.assertGreater(first.job_list.table.height(), before)
            self.assertGreater(first.job_list.table.height(), 160)
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

    def test_active_jobs_group_by_submitted_name_and_show_selected_slurm_times(self):
        dialog = self.dialog()
        tab = dialog.active_jobs
        for job_id, username, name in [('1', 'tester', 'A'), ('2', 'tester', 'A'), ('3', 'other', 'Private')]:
            write_record(self.directory / 'jobs' / f'{job_id}.json',
                         dict(host=HOST, username=username, job_id=job_id, state='RUNNING', study_name=name))
        output = (
            '1|RUNNING|2026-11-18T04:15:00|1-05:20:40|2:17:15|RM-1\n'
            '2|PENDING|2026-11-18T23:15:00|0:00|8:00:00|RM-2\n'
            '3|RUNNING|2026-11-18T05:00:00|25:40|UNLIMITED|External job\n'
            '4|COMPLETED|2026-11-18T04:15:00|1:30|0:00|Done\n'
        )
        with patch.object(dialog, 'analysis_auth', return_value=RemoteAuth('tester', 'test')), patch(
                'truba_active_jobs.RemoteTransport') as transport:
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
            self.assertIn('%i|%T|%S|%M|%L|%j', command)
            self.assertEqual([tab.works.item(i, 0).text() for i in range(tab.works.rowCount())],
                             ['A', 'External job'])
            self.assertEqual([tab.jobs.item(i, 0).text() for i in range(tab.jobs.rowCount())], ['1', '2'])
            self.assertNotEqual(tab.started.text(), '—')
            self.assertEqual(tab.elapsed.text(), '29:20:40')
            self.assertEqual(tab.remaining.text(), '02:17:15')
            tab.jobs.setCurrentCell(1, 0)
            tab.show_groups(tab.groups)
            self.assertEqual(tab.jobs.currentItem().text(), '2')
            self.assertEqual(tab.started.text(), '—')
            self.assertEqual(tab.remaining.text(), '—')
            tab.works.setCurrentCell(1, 0)
            self.assertEqual(tab.jobs.item(0, 0).text(), '3')
            self.assertEqual(tab.elapsed.text(), '00:25:40')
            dialog.connection_changed.emit(False)
            self.assertEqual(tab.works.rowCount(), 0)
            self.assertEqual(tab.jobs.rowCount(), 0)

    def test_active_jobs_columns_restore_after_reopening(self):
        store = WindowStateStore(self.app, QSettings(str(self.directory / 'active.ini'), QSettings.Format.IniFormat))
        try:
            first = self.dialog()
            first.resize(690, 650)
            first.tabs.setCurrentWidget(first.active_jobs)
            first.show()
            self.app.processEvents()
            first.active_jobs.splitter.moveSplitter(250, 1)
            expected = first.active_jobs.splitter.sizes()
            first.hide()
            second = self.dialog()
            second.show()
            second.tabs.setCurrentWidget(second.active_jobs)
            self.app.processEvents()
            self.assertEqual(second.active_jobs.splitter.sizes(), expected)
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
        account = dialog.settings_layout.itemAt(1).widget()
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
            with patch("truba_connection.paramiko.SSHClient", LocalClient), patch(
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
