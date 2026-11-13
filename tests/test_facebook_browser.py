"""Secure session plumbing tests. No real account, passwords, cookies or paid calls."""
import os
import socket
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

_tmp = tempfile.TemporaryDirectory()
for k, v in {'DATA_DIR': _tmp.name, 'DB_PATH': _tmp.name+'/browser.db', 'UPLOAD_DIR': _tmp.name+'/uploads',
             'RENDER_DIR': _tmp.name+'/renders', 'TEMPLATE_DIR': _tmp.name+'/templates', 'DISABLE_SCHEDULER': 'true'}.items():
    os.environ[k] = v
from app import main, facebook_browser as browser, facebook_reader as reader
from app.social_publish import SocialError
from fastapi.testclient import TestClient


class BrowserTests(unittest.TestCase):
    def test_browser_is_limited_to_https_facebook_resources(self):
        for url in ['https://www.facebook.com/login/', 'https://scontent.example.fbcdn.net/image', 'https://connect.facebook.net/script']:
            self.assertTrue(browser._allowed_url(url))
        for url in ['http://www.facebook.com/', 'https://facebook.com.evil.invalid/', 'file:///etc/passwd', 'http://127.0.0.1/', 'https://example.org/']:
            self.assertFalse(browser._allowed_url(url))

    def test_arkose_resources_only_load_during_manual_interaction(self):
        for url in ['https://client-api.arkoselabs.com/v2/fixture/api.js', 'https://iframe.arkoselabs.com/frame', 'https://client-api.funcaptcha.com/fc/fixture']:
            self.assertFalse(browser._allowed_resource_url(url))
            self.assertTrue(browser._allowed_resource_url(url, interactive=True))
        for url in ['http://client-api.arkoselabs.com/', 'https://arkoselabs.com.evil.invalid/', 'https://evilfuncaptcha.com/', 'https://user@client-api.arkoselabs.com/']:
            self.assertFalse(browser._allowed_resource_url(url, interactive=True))

    def test_facebook_sandbox_challenge_is_allowed_only_on_exact_manual_path(self):
        url='https://www.fbsbx.com/captcha/arkose/iframe/?fixture=public-test'
        self.assertTrue(browser._allowed_resource_url(url, interactive=True))
        self.assertFalse(browser._allowed_resource_url(url))
        for url in ['https://www.fbsbx.com/other/resource', 'https://www.fbsbx.com.evil.invalid/captcha/arkose/iframe/',
                    'http://www.fbsbx.com/captcha/arkose/iframe/', 'https://user@www.fbsbx.com/captcha/arkose/iframe/']:
            self.assertFalse(browser._allowed_resource_url(url, interactive=True))
        for top_level in [True,False]:
            route=Mock()
            route.request.url='https://www.fbsbx.com/captcha/arkose/iframe/'
            route.request.is_navigation_request.return_value=True
            route.request.frame.parent_frame=None if top_level else Mock()
            with patch.object(browser,'_interactive',True):browser._route_request(route)
            if top_level:route.abort.assert_called_once()
            else:route.continue_.assert_called_once()

    def test_challenge_frame_allowed_but_top_level_navigation_blocked(self):
        for top_level in [False, True]:
            route = Mock()
            route.request.url = 'https://iframe.arkoselabs.com/fixture'
            route.request.is_navigation_request.return_value = True
            route.request.frame.parent_frame = None if top_level else Mock()
            with patch.object(browser, '_interactive', True):
                browser._route_request(route)
            if top_level: route.abort.assert_called_once()
            else: route.continue_.assert_called_once()

    def test_stale_container_singletons_recovered_without_touching_cookies(self):
        with tempfile.TemporaryDirectory() as d, patch.object(browser, '_profile', Path(d)), patch.dict(os.environ, {'RAILWAY_SERVICE_ID': 'fixture'}):
            cookies = Path(d)/'Default'/'Cookies'
            cookies.parent.mkdir()
            cookies.write_bytes(b'fixture-session-do-not-remove')
            for name in ['SingletonLock','SingletonSocket','SingletonCookie']:
                (Path(d)/name).symlink_to('previous-container-999999')
            try:
                browser._acquire_profile_guard()
                browser._clear_obsolete_singletons()
                self.assertFalse((Path(d)/'SingletonLock').is_symlink())
                self.assertEqual(cookies.read_bytes(), b'fixture-session-do-not-remove')
            finally:
                browser._release_profile_guard()

    def test_live_browser_lock_is_not_removed(self):
        with tempfile.TemporaryDirectory() as d, patch.object(browser, '_profile', Path(d)), patch.object(browser, '_pid_owns_profile', return_value=True):
            lock=Path(d)/'SingletonLock'
            lock.symlink_to(socket.gethostname()+'-123')
            try:
                browser._acquire_profile_guard()
                with self.assertRaisesRegex(SocialError, 'usando esta sesión'):
                    browser._clear_obsolete_singletons()
                self.assertTrue(lock.is_symlink())
            finally:
                browser._release_profile_guard()

    def test_other_process_guard_prevents_recovery(self):
        import fcntl
        with tempfile.TemporaryDirectory() as d, patch.object(browser, '_profile', Path(d)):
            fd=os.open(Path(d)/'.desk-browser.lock',os.O_CREAT|os.O_RDWR,0o600)
            try:
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaisesRegex(SocialError, 'otro proceso'):
                    browser._acquire_profile_guard()
                self.assertIsNone(browser._profile_guard)
            finally:
                os.close(fd)

    def test_foreign_lock_outside_railway_is_not_removed(self):
        with tempfile.TemporaryDirectory() as d, patch.object(browser, '_profile', Path(d)), patch.dict(os.environ, {'RAILWAY_SERVICE_ID':'','RAILWAY_DEPLOYMENT_ID':''}):
            lock=Path(d)/'SingletonLock';lock.symlink_to('foreign-host-123')
            try:
                browser._acquire_profile_guard()
                with self.assertRaisesRegex(SocialError, 'otro equipo'):
                    browser._clear_obsolete_singletons()
                self.assertTrue(lock.is_symlink())
            finally:
                browser._release_profile_guard()

    def test_startup_error_diagnosis_never_exposes_values(self):
        for message, expected in [('ProcessSingleton fixture-sensitive','perfil en uso'), ('Permission denied fixture-sensitive','permiso'), ('SIGKILL fixture-sensitive','memoria'), ('unknown fixture-sensitive','motor')]:
            result=str(browser._startup_failure(RuntimeError(message)))
            self.assertIn(expected,result)
            self.assertNotIn('fixture-sensitive',result)

    def test_worker_does_not_return_browser_exception_values(self):
        from app.facebook_browser_runtime import _worker
        connection = Mock()
        connection.recv.side_effect = [('input', {'kind': 'text', 'text': 'fixture-sensitive-input'}), EOFError()]
        with patch.object(browser, '_execute', side_effect=[RuntimeError('fixture-sensitive-input'), {'open': False}]), patch.object(browser, '_close'), patch.object(browser, '_profile'):
            _worker(connection, '/tmp/fixture-unused-browser')
        response = connection.send.call_args.args[0]
        self.assertFalse(response['ok'])
        self.assertNotIn('fixture-sensitive-input', str(response))

    def test_input_validation_prevents_commands_and_out_of_bounds_clicks(self):
        page = Mock()
        with patch.object(browser, '_ensure'), patch.object(browser, '_context', Mock()), patch.object(browser, '_page', page):
            for payload in [{'kind': 'key', 'key': 'F12'}, {'kind': 'click', 'x': -1, 'y': 5},
                            {'kind': 'click', 'x': 1, 'y': 9999}, {'kind': 'text', 'text': 'x'*513}, {'kind': 'evaluate', 'code': 'fixture'}]:
                with self.assertRaises(SocialError): browser._execute('input', payload)
        self.assertEqual(page.mock_calls, [])

    def test_user_confirm_cannot_skip_visible_login_form(self):
        page = Mock(url='https://www.facebook.com/login/')
        page.locator.return_value.count.return_value = 1
        with patch.object(browser, '_ensure'), patch.object(browser, '_context', Mock()), patch.object(browser, '_page', page), patch.object(browser, '_allowed_url', return_value=True), self.assertRaisesRegex(SocialError, 'formulario'):
            browser._execute('confirm', {})

    def test_reader_only_uses_browser_after_explicit_connection(self):
        with patch.dict(os.environ, {'FACEBOOK_READER_PROVIDER': 'public'}), patch.object(reader.db, 'get_setting', return_value='true'):
            self.assertEqual(reader.configuration()['provider'], 'browser')
        with patch.dict(os.environ, {'FACEBOOK_READER_PROVIDER': 'public'}), patch.object(reader.db, 'get_setting', return_value=''):
            self.assertEqual(reader.configuration()['provider'], 'public')

    def test_all_browser_routes_require_desk_authentication(self):
        client = TestClient(main.app)
        for method, path, kwargs in [('GET', '/api/facebook/browser/status', {}),
                                     ('GET', '/api/facebook/browser/screenshot', {}),
                                     ('POST', '/api/facebook/browser/start', {'json': {}}),
                                     ('POST', '/api/facebook/browser/input', {'json': {'kind': 'text', 'text': 'fixture'}}),
                                     ('POST', '/api/facebook/browser/restart', {'json': {}})]:
            with patch.object(browser, 'call') as call:
                response = client.request(method, path, **kwargs)
            self.assertIn(response.status_code, (401, 503))
            call.assert_not_called()

    def test_private_screenshot_is_not_cacheable(self):
        main.app.dependency_overrides[main.require_auth] = lambda: True
        try:
            with patch.object(browser, 'call', side_effect=[b'fixture-png', {'blocked_hosts': []}]):
                response = TestClient(main.app).get('/api/facebook/browser/screenshot')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(response.headers['Content-Type'], 'image/png')
        finally:
            main.app.dependency_overrides.pop(main.require_auth, None)


if __name__ == '__main__': unittest.main()
