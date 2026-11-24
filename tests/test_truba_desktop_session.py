"""The desktop connection supplies an SSH handle with no stored credentials."""

from unittest.mock import patch

import pytest
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from truba_connection import TrubaConnectionDialog
from truba_desktop import DesktopStatus
from truba_jobs import SSHJobs, connection
from truba_remote import RemoteContext
from truba_shared_session import DesktopSession, DesktopSSHClient


def test_desktop_panel_uses_the_advertised_session_and_never_reads_credentials():
    app = QApplication.instance() or QApplication([])
    with patch("truba_connection.CredentialStore") as credentials:
        dialog = TrubaConnectionDialog(use_desktop_session=True)
    try:
        credentials.assert_not_called()
        assert dialog.credentials is None
        assert dialog.password.isHidden()
        assert dialog.analysis_auth() is None
        assert not dialog.start_saved_connection()
        assert dialog.job_list.cancellation_auth() is None
        opened = QSignalSpy(dialog.open_desktop_requested)
        dialog.connect_button.click()
        assert opened.count() == 1 and dialog.worker is None

        session = DesktopSession("tester", 10000, "a" * 64)
        dialog._desktop_status_changed(DesktopStatus(session))
        assert dialog.connected and dialog.analysis_auth() is session
        assert "tester" in dialog.status.text()
        assert dialog.worker is None
        assert dialog.job_list.cancellation_auth() is session

        dialog._desktop_status_changed(DesktopStatus(other_cluster=True))
        assert not dialog.connected and dialog.analysis_auth() is None
        assert dialog.job_list.cancellation_auth() is None
    finally:
        dialog.shutdown()
        dialog.close()
        app.processEvents()


def test_ssh_delegate_uses_desktop_session_even_for_old_winscp_records():
    session = DesktopSession("tester", 10000, "a" * 64)
    with patch("truba_ssh.paramiko.SSHClient") as direct, patch("truba_jobs.get_auth") as saved:
        delegate = connection(RemoteContext(transport="auto"), session, {"transport": "winscp"})
        assert isinstance(delegate, SSHJobs)
        with delegate.connect() as client:
            assert isinstance(client, DesktopSSHClient)
            assert client.session is session
        with pytest.raises(ConnectionError, match="TRUBA ARF"):
            connection(RemoteContext(transport="auto"))
        direct.assert_not_called()
        saved.assert_not_called()
