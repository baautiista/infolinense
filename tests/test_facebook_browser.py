"""Secure session plumbing tests. No real account, passwords, cookies or paid calls."""
import os
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

    def test_worker_does_not_return_browser_exception_values(self):
        with patch.object(browser, '_execute', side_effect=RuntimeError('fixture-sensitive-input')), self.assertRaises(SocialError) as error:
            browser.call('status')
        self.assertNotIn('fixture-sensitive-input', str(error.exception))

    def test_input_validation_prevents_commands_and_out_of_bounds_clicks(self):
        page = Mock()
        with patch.object(browser, '_ensure'), patch.object(browser, '_page', page):
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
                                     ('POST', '/api/facebook/browser/input', {'json': {'kind': 'text', 'text': 'fixture'}})]:
            with patch.object(browser, 'call') as call:
                response = client.request(method, path, **kwargs)
            self.assertIn(response.status_code, (401, 503))
            call.assert_not_called()

    def test_private_screenshot_is_not_cacheable(self):
        main.app.dependency_overrides[main.require_auth] = lambda: True
        try:
            with patch.object(browser, 'call', return_value=b'fixture-png'):
                response = TestClient(main.app).get('/api/facebook/browser/screenshot')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(response.headers['Content-Type'], 'image/png')
        finally:
            main.app.dependency_overrides.pop(main.require_auth, None)


if __name__ == '__main__': unittest.main()
