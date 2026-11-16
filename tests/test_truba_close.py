"""Closing releases only the owned session, including pending submissions."""
from concurrent.futures import Future, CancelledError
import unittest
from unittest.mock import patch

from remote_controller import release_on_close, release_while_busy


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

    def test_waits_for_submission_then_releases_that_exact_job(self):
        submitted = Future()
        session = dict(job_id='321', state='PENDING')
        submitted.set_result(session)
        runtime = Future()
        runtime.set_exception(CancelledError())
        with patch('remote_controller.release_session', return_value=dict(session, state='released')) as release:
            result = release_on_close(None, submitted, runtime, None, 'auth')
        release.assert_called_once_with(session, 'auth')
        self.assertEqual(result['state'], 'released')

    def test_unconfirmed_submission_recovers_the_durable_record_before_release(self):
        submitted = Future()
        submitted.set_exception(RuntimeError('submission reply lost'))
        session = dict(job_id='321', state='RUNNING')
        with patch('remote_controller.saved_session', return_value=session), patch(
            'remote_controller.release_session', return_value=dict(session, state='released')
        ) as release:
            release_on_close(None, submitted, None, None, None)
        release.assert_called_once_with(session, None)

    def test_release_failure_is_not_reported_as_successful_close(self):
        with patch('remote_controller.release_session', side_effect=RuntimeError('VPN disconnected')):
            with self.assertRaisesRegex(RuntimeError, 'VPN disconnected'):
                release_on_close(dict(job_id='321', state='RUNNING'), None, None, None, None)

    def test_failed_standalone_preview_cancellation_does_not_silently_close(self):
        preview = Future()
        preview.set_exception(RuntimeError('Cancellation was not confirmed'))
        with self.assertRaisesRegex(RuntimeError, 'not confirmed'):
            release_on_close(None, None, None, preview, None)

    def test_confirmed_parent_release_is_enough_if_its_step_already_ended(self):
        preview = Future()
        preview.set_exception(RuntimeError('Parent job ended during retrieval'))
        with patch('remote_controller.release_session', return_value=dict(state='released')):
            result = release_on_close(dict(job_id='321'), None, None, preview, None)
        self.assertEqual(result['state'], 'released')


if __name__ == '__main__':
    unittest.main()
