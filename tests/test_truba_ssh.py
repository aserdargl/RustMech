"""Authentication reuse, session ownership and failed-exchange behavior."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import paramiko
from truba_jobs import RemoteAuth, SSHJobs
from truba_ssh import (register_session, unregister_session, close_owned_sessions)


class SharedSshTests(unittest.TestCase):
    def setUp(self):
        close_owned_sessions()
        self.auth = RemoteAuth('tester', 'test-password')
        self.addCleanup(close_owned_sessions)

    def test_commands_and_transfers_authenticate_only_once(self):
        client = MagicMock()
        with patch('truba_ssh.paramiko.SSHClient', return_value=client):
            for _ in range(4):
                with SSHJobs(self.auth).connect() as connected:
                    self.assertIs(connected, client)
            client.connect.assert_called_once()
            self.assertFalse(client.connect.call_args.kwargs['allow_agent'])
            self.assertFalse(client.connect.call_args.kwargs['look_for_keys'])
            client.close.assert_not_called()
            close_owned_sessions()
            client.close.assert_called_once()

    def test_panel_connection_is_borrowed_without_authenticating_or_closing(self):
        client = MagicMock()
        register_session(self.auth.username, self.auth.password, client)
        try:
            with patch('truba_ssh.paramiko.SSHClient') as create:
                with SSHJobs(self.auth).connect() as connected:
                    self.assertIs(connected, client)
                close_owned_sessions()
                create.assert_not_called()
                client.close.assert_not_called()
        finally:
            unregister_session(self.auth.username, client)

    def test_rejected_authentication_is_not_retried(self):
        client = MagicMock()
        client.connect.side_effect = paramiko.AuthenticationException()
        with patch('truba_ssh.paramiko.SSHClient', return_value=client):
            with self.assertRaises(paramiko.AuthenticationException):
                with SSHJobs(self.auth).connect():
                    self.fail('Authenticated despite rejection')
        client.connect.assert_called_once()
        client.close.assert_called_once()

    def test_cancellation_channel_can_open_during_an_active_transfer(self):
        client = MagicMock()
        def another_channel():
            with SSHJobs(self.auth).connect() as connected:
                return connected is client
        with patch('truba_ssh.paramiko.SSHClient', return_value=client), ThreadPoolExecutor(max_workers=1) as executor:
            with SSHJobs(self.auth).connect():
                self.assertTrue(executor.submit(another_channel).result(timeout=2))
            client.connect.assert_called_once()

    def test_missing_optional_file_does_not_trigger_another_login(self):
        client = MagicMock()
        with patch('truba_ssh.paramiko.SSHClient', return_value=client):
            with self.assertRaises(FileNotFoundError):
                with SSHJobs(self.auth).connect():
                    raise FileNotFoundError('Optional log is absent')
            with SSHJobs(self.auth).connect() as connected:
                self.assertIs(connected, client)
            client.connect.assert_called_once()

    def test_broken_exchange_is_not_replayed_and_next_request_reconnects(self):
        first, second = MagicMock(), MagicMock()
        with patch('truba_ssh.paramiko.SSHClient', side_effect=[first, second]) as create:
            with self.assertRaises(EOFError):
                with SSHJobs(self.auth).connect():
                    raise EOFError('Connection lost after submission')
            self.assertEqual(create.call_count, 1)
            first.close.assert_called_once()
            with SSHJobs(self.auth).connect() as client:
                self.assertIs(client, second)
            self.assertEqual(create.call_count, 2)
