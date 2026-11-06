"""Synthetic API fixtures only; never used as publications in the application."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

_tmp = tempfile.TemporaryDirectory()
os.environ['DATA_DIR'] = _tmp.name
os.environ['DB_PATH'] = _tmp.name + '/reader.db'
os.environ['UPLOAD_DIR'] = _tmp.name + '/uploads'
os.environ['RENDER_DIR'] = _tmp.name + '/renders'
os.environ['TEMPLATE_DIR'] = _tmp.name + '/templates'
from app import facebook_reader as reader
from app import social_publish as meta

NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
PAGE = {'id': '123', 'name': 'Test page', 'category': 'Religious organization',
        'username': 'test.page', 'link': 'https://www.facebook.com/test.page'}


def post(number, days=1, author='123', **changes):
    result = {'id': '123_' + number, 'from': {'id': author}, 'message': 'Fixture only',
              'created_time': (NOW - timedelta(days=days)).isoformat(),
              'permalink_url': 'https://www.facebook.com/test.page/posts/' + number,
              'full_picture': 'https://example.org/fixture.jpg'}
    return dict(result, **changes)


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'FACEBOOK_READER_PROVIDER': 'meta', 'FACEBOOK_ALLOW_PAID': 'false'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def read(self, pages, url='https://www.facebook.com/test.page', metadata=PAGE):
        calls = []
        def graph(method, path, params):
            calls.append((path, dict(params)))
            return metadata if path == '/123' else pages.pop(0)
        with patch.object(meta, '_managed_pages', return_value=[{'id': '123', 'access_token': 'fixture'}]), patch.object(meta, '_graph', side_effect=graph):
            result = reader.page_posts(url, NOW)
        return result, calls

    def test_author_dates_pagination_and_order(self):
        first = {'data': [post('1', 6), post('2', 8), post('3', -1), post('4', author='999'),
                          post('5', created_time='unknown'), post('6', created_time='2026-11-05')],
                 'paging': {'next': 'https://untrusted.invalid/token', 'cursors': {'after': 'cursor'}}}
        second = {'data': [post('7', 0), post('1', 6), post('8', id='999_8')]}
        result, calls = self.read([first, second])
        self.assertEqual([p['id'] for p in result], ['123_7', '123_1'])
        self.assertEqual(result[0]['page_id'], '123')
        self.assertEqual(result[0]['image'], 'https://example.org/fixture.jpg')
        self.assertEqual(calls[-1][0], '/123/posts')
        self.assertEqual(calls[-1][1]['after'], 'cursor')
        self.assertIn('from', calls[-1][1]['fields'])

    def test_exact_seven_day_boundary_and_picture_only(self):
        result, _ = self.read([{'data': [post('1', 7, message=''), post('2', 7.00001)]}])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['text'], '')

    def test_same_name_never_matches_wrong_page(self):
        with self.assertRaises(meta.SocialError):
            self.read([], url='https://www.facebook.com/unrelated')

    def test_personal_profile_cannot_qualify(self):
        with self.assertRaises(meta.SocialError):
            self.read([], url='https://www.facebook.com/profile.php?id=123', metadata={'id': '123', 'name': 'Test page'})

    def test_numeric_page_and_missing_author(self):
        result, _ = self.read([{'data': [post('1', **{'from': {}}), post('2')]}], url='https://www.facebook.com/profile.php?id=123')
        self.assertEqual([p['id'] for p in result], ['123_2'])

    def test_reject_host_spoofing_groups_and_post_urls(self):
        for url in ['https://facebook.com.evil.invalid/test.page', 'https://www.facebook.com/groups/123',
                    'https://www.facebook.com/test.page/posts/1', 'https://www.facebook.com/profile.php']:
            self.assertEqual(reader.account(url), '')

    def test_official_approval_no_fallback_and_safe_errors(self):
        sources = [{'id': 1, 'name': 'Unconfirmed', 'url': PAGE['link'], 'official': 0},
                   {'id': 2, 'name': 'Confirmed', 'url': 'https://facebook.com/123', 'official': 1}]
        with patch.object(reader.db, 'rows', return_value=sources), patch.object(reader, 'page_posts', side_effect=meta.SocialError('secret fixture')) as read:
            result = reader.recent()
        read.assert_called_once_with('https://facebook.com/123')
        self.assertEqual(result['items'], [])
        self.assertEqual([p['status'] for p in result['pages']], ['unverified', 'unavailable'])
        self.assertNotIn('secret fixture', str(result))

    def test_global_order_and_brotherhood_attribution(self):
        sources = [{'id': 1, 'name': 'First brotherhood', 'url': PAGE['link'], 'official': 1},
                   {'id': 2, 'name': 'Second brotherhood', 'url': 'https://facebook.com/456', 'official': 1}]
        older = dict(post('1', 3), published_at=(NOW-timedelta(days=3)).isoformat(), page_id='123')
        newer = dict(post('2', 1), id='456_2', published_at=(NOW-timedelta(days=1)).isoformat(), page_id='456')
        with patch.object(reader.db, 'rows', return_value=sources), patch.object(reader, 'page_posts', side_effect=[[older], [newer]]):
            result = reader.recent()
        self.assertEqual([p['brotherhood'] for p in result['items']], ['Second brotherhood', 'First brotherhood'])
        self.assertEqual([p['source_id'] for p in result['items']], [2, 1])
        self.assertTrue(all(p['status'] == 'ok' for p in result['pages']))

    def test_configuration_selection_and_safe_diagnostics(self):
        from app.facebook_reader import configuration
        cases = [('', 'fixture-token', 'fixture-task', 'public'),
                 ('meta', 'fixture-token', 'fixture-task', 'meta'),
                 ('"apify"', 'fixture-token', 'fixture-task', 'public'),
                 ('', '', '', 'public'), ('wrong', '', '', 'invalid')]
        for provider, token, task, expected in cases:
            with patch.dict(os.environ, {'FACEBOOK_READER_PROVIDER': provider, 'APIFY_TOKEN': token, 'APIFY_FACEBOOK_TASK_ID': task}):
                result = configuration()
            self.assertEqual(result['provider'], expected)
            self.assertNotIn('fixture-token', str(result))
            self.assertNotIn('fixture-task', str(result))
            self.assertEqual(result['apify_token_present'], bool(token))

    def test_environment_page_tokens(self):
        with patch.object(meta.db, 'rows', return_value=[]), patch.object(meta, 'META_PAGE_ID', '123'), patch.object(meta, 'META_PAGE_TOKEN', 'fixture'), patch.dict(os.environ, {'META_READER_PAGE_TOKENS': '{"456":"another fixture"}'}):
            self.assertEqual({p['id'] for p in meta._managed_pages()}, {'123', '456'})


if __name__ == '__main__':
    unittest.main()
