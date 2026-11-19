"""Explicit resource release, including pending submissions."""
from concurrent.futures import Future
import unittest
from unittest.mock import patch

from remote_controller import release_while_busy


class TrubaCloseTests(unittest.TestCase):
    def test_explicit_release_is_sent_before_waiting_for_inflight_watch(self):
        from unittest.mock import Mock
        pending = Mock()
        session = dict(job_id='321', state='RUNNING')
        released = dict(session, state='released')
        events = []
        pending.result.side_effect = lambda: events.append('watch completed')
        with patch('remote_controller.release_session', side_effect=lambda *_: events.append('cancel sent') or released), patch('truba_session.save_released_session', return_value=released):
            self.assertEqual(release_while_busy(session, pending, False, 'auth'), released)
        self.assertEqual(events, ['cancel sent', 'watch completed'])

    def test_cancel_during_allocation_waits_for_that_submission_only(self):
        pending = Future()
        session = dict(job_id='321', state='PENDING')
        pending.set_result(session)
        with patch('remote_controller.release_session', return_value=dict(session, state='released')) as release:
            release_while_busy(None, pending, True, 'auth')
        release.assert_called_once_with(session, 'auth')

if __name__ == '__main__':
    unittest.main()
