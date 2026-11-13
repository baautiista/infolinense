import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tests import test_facebook_browser as fixtures  # Isolate DB before importing app modules.
from app import db, main
from app.facebook_browser_runtime import BrowserRuntime, BrowserBusy, BrowserUnavailable, _identity
from tests.facebook_browser_helpers import controlled_worker, chromium_worker
from fastapi.testclient import TestClient


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.profile = Path(self.temp.name) / 'profile'
        self.profile.mkdir()
        self.marker = self.profile / 'saved-session-marker'
        self.marker.write_bytes(b'preserve-the-existing-profile')
        self.runtime = BrowserRuntime(worker=controlled_worker, read_timeout=2)
        self.patch_db = patch.object(db, 'get_setting', return_value='true')
        self.patch_db.start()

    def tearDown(self):
        self.runtime.call('restart', self.profile)
        self.patch_db.stop()
        self.temp.cleanup()

    def wait_started(self):
        end = time.monotonic() + 10
        while time.monotonic() < end:
            if (self.profile / 'started').exists():
                return
            time.sleep(.01)
        self.fail('Fixture worker did not start')

    def run_blocked(self, **kwargs):
        errors = []
        def run():
            try:
                self.runtime.call('read', self.profile, block=True, **kwargs)
            except BrowserUnavailable as error:
                errors.append(error)
        thread = threading.Thread(target=run)
        thread.start()
        self.wait_started()
        return thread, errors

    def test_hung_command_cannot_fill_a_queue_and_status_still_responds(self):
        thread, errors = self.run_blocked()
        try:
            before = time.monotonic()
            for _ in range(40):
                with self.assertRaises(BrowserBusy):
                    self.runtime.call('screenshot', self.profile)
                state = self.runtime.call('status', self.profile)
                self.assertTrue(state['busy'])
                self.assertEqual(state['action'], 'read')
            self.assertLess(time.monotonic() - before, 1)
        finally:
            self.runtime.call('restart', self.profile)
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertEqual(self.marker.read_bytes(), b'preserve-the-existing-profile')
        self.assertTrue(self.runtime.call('read', self.profile)['done'])

    def test_timeout_ends_worker_and_next_read_succeeds(self):
        with self.assertRaisesRegex(BrowserUnavailable, 'recuperado'):
            self.runtime.call('read', self.profile, block=True)
        self.assertFalse(self.runtime.status()['busy'])
        self.assertTrue(self.runtime.call('read', self.profile)['done'])
        self.assertTrue(self.marker.exists())

    def test_worker_crash_does_not_leave_browser_occupied(self):
        with self.assertRaises(BrowserUnavailable):
            self.runtime.call('read', self.profile, crash=True)
        self.assertTrue(self.runtime.call('read', self.profile)['done'])

    def test_recovery_terminates_only_owned_processes(self):
        unrelated = os.getpid()
        thread, _ = self.run_blocked(child=True)
        child = int((self.profile / 'child-pid').read_text())
        child_start = _identity(child)[1]
        self.runtime.call('restart', self.profile)
        thread.join(5)
        os.kill(unrelated, 0)
        # An exited child may remain a zombie briefly until the container reaps it.
        if _identity(child) and _identity(child)[1] == child_start:
            fields = Path('/proc/%s/stat' % child).read_text().rsplit(')', 1)[1].split()
            self.assertEqual(fields[0], 'Z')
        self.assertTrue(self.marker.exists())

    def test_busy_api_is_retryable_and_recovery_requires_auth(self):
        main.app.dependency_overrides[main.require_auth] = lambda: True
        try:
            with patch('app.facebook_browser.call', side_effect=BrowserBusy('Ocupado')):
                response = TestClient(main.app).get('/api/facebook/browser/screenshot')
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.headers['Retry-After'], '2')
        finally:
            main.app.dependency_overrides.pop(main.require_auth, None)
        with patch('app.facebook_browser.call') as command:
            response = TestClient(main.app).post('/api/facebook/browser/restart', json={})
        self.assertIn(response.status_code, (401, 503))
        command.assert_not_called()

    @unittest.skipUnless(shutil.which('chromium'), 'Chromium is not installed')
    def test_real_chromium_recovers_with_existing_profile(self):
        self.runtime.call('restart', self.profile)
        self.runtime = BrowserRuntime(worker=chromium_worker, read_timeout=4, command_timeout=30)
        result = self.runtime.call('start', self.profile, save_fixture_session=True)
        self.assertIn('fixture', result['chromium'])
        self.assertTrue(result['session_present'])
        with self.assertRaises(BrowserUnavailable):
            self.runtime.call('read', self.profile, block=True)
        self.assertTrue(self.marker.exists())
        result = self.runtime.call('start', self.profile)
        self.assertIn('fixture', result['chromium'])
        self.assertTrue(result['session_present'])


if __name__ == '__main__':
    unittest.main()
