"""Synthetic provider fixtures, never used as live publications."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from app import facebook_apify as apify, facebook_reader as reader
from app.social_publish import SocialError

NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
URL = 'https://www.facebook.com/fixture.page'


def post(number='1', **changes):
    row = {'page': {'id': '123', 'url': URL, 'name': 'Fixture Page', 'isPage': True},
           'user': {'id': '123', 'profileUrl': URL}, 'postId': number,
           'url': URL + '/posts/' + number, 'time': (NOW-timedelta(days=1)).isoformat(),
           'text': 'Test only', 'media': [{'thumbnail': 'https://example.org/test.jpg'}]}
    row.update(changes)
    return row


class ApifyTests(unittest.TestCase):
    def test_valid_authorship_image_and_deduplication(self):
        result = apify.page_posts(URL, [post(), post()], NOW)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['page_id'], '123')
        self.assertEqual(result[0]['image'], 'https://example.org/test.jpg')
        self.assertEqual(result[0]['provider'], 'apify')

    def test_third_parties_profiles_missing_authors_and_foreign_links(self):
        rows = [post(user={'id': '999'}), post(page={'id': '123', 'url': URL}),
                post(user={}), post(url='https://facebook.com/other/posts/1'),
                post(url='https://facebook.com.evil.invalid/fixture.page/posts/1')]
        with self.assertRaises(SocialError):
            apify.page_posts(URL, rows, NOW)

    def test_observed_actor_schema_and_numeric_permalink(self):
        row = post(page={}, facebookUrl=URL, facebookId='123', pageAdLibrary={'id': '456'},
                   topLevelUrl='https://www.facebook.com/123/posts/1')
        self.assertEqual(apify.page_posts(URL, [row], NOW)[0]['page_id'], '123')
        reel = dict(row, url='https://www.facebook.com/reel/789/')
        self.assertEqual(apify.page_posts(URL, [reel], NOW)[0]['url'], row['topLevelUrl'])
        for changes in [{'facebookId': '999'}, {'pageAdLibrary': {}},
                        {'topLevelUrl': 'https://www.facebook.com/999/posts/1'},
                        {'topLevelUrl': 'https://www.facebook.com/123/posts/2'}]:
            with self.assertRaises(SocialError):
                apify.page_posts(URL, [dict(row, **changes)], NOW)
        self.assertEqual(apify.page_posts(URL, [dict(row, sharedPost={'text': 'Third party'})], NOW), [])

    def test_actor_error_records_do_not_become_publications(self):
        for key in ['url', 'inputUrl']:
            with self.assertRaisesRegex(SocialError, 'no pudo acceder'):
                apify.page_posts(URL, [{key: URL, 'error': 'not_available'}], NOW)

    def test_window_and_sort(self):
        rows = [post('1', time=(NOW-timedelta(days=7)).isoformat()), post('2', time=NOW.isoformat()),
                post('3', time=(NOW-timedelta(days=8)).isoformat()),
                post('4', time=(NOW+timedelta(seconds=1)).isoformat()), post('5', time='2026-11-05'), post('6', time='unknown')]
        self.assertEqual([p['id'] for p in apify.page_posts(URL, rows, NOW)], ['123:2', '123:1'])

    def test_missing_page_is_not_claimed_empty(self):
        with self.assertRaises(SocialError):
            apify.page_posts('https://facebook.com/other.page', [post()], NOW)

    def test_fetch_resolves_fixed_dataset_and_paginates_without_starting_runs(self):
        env = {'APIFY_TOKEN': 'fixture-secret', 'APIFY_FACEBOOK_TASK_ID': 'fixture-task'}
        run = {'data': {'status': 'SUCCEEDED', 'finishedAt': NOW.isoformat(), 'defaultDatasetId': 'fixed-id'}}
        with patch.dict(os.environ, env), patch.object(apify, '_get', side_effect=[run, [post()]*500, [post('2')]]) as get:
            rows, finished = apify.fetch(NOW)
        self.assertEqual(len(rows), 501)
        self.assertEqual(get.call_args_list[1].args[0], '/datasets/fixed-id/items')
        self.assertEqual(get.call_args_list[2].kwargs['offset'], 500)
        self.assertEqual(finished, NOW.isoformat())

    def test_console_task_name_with_slash_is_accepted(self):
        env = {'APIFY_TOKEN': 'fixture', 'APIFY_FACEBOOK_TASK_ID': 'fixture_user/task-name'}
        run = {'data': {'status': 'SUCCEEDED', 'finishedAt': NOW.isoformat(), 'defaultDatasetId': 'fixed-id'}}
        with patch.dict(os.environ, env), patch.object(apify, '_get', side_effect=[run, []]) as get:
            apify.fetch(NOW)
        self.assertEqual(get.call_args_list[0].args[0], '/actor-tasks/fixture_user~task-name/runs/last')

    def test_stale_failed_or_malformed_run(self):
        env = {'APIFY_TOKEN': 'fixture', 'APIFY_FACEBOOK_TASK_ID': 'fixture-task'}
        for data in [{'status': 'RUNNING'}, {'status': 'SUCCEEDED', 'finishedAt': (NOW-timedelta(days=2)).isoformat(), 'defaultDatasetId': 'id'}]:
            with patch.dict(os.environ, env), patch.object(apify, '_get', return_value={'data': data}), self.assertRaises(SocialError):
                apify.fetch(NOW)

    def test_token_in_header_only_and_errors_redacted(self):
        response = Mock(status_code=403)
        with patch.object(apify.requests, 'get', return_value=response) as get, self.assertRaises(SocialError) as error:
            apify._get('/test', 'fixture-secret')
        self.assertNotIn('fixture-secret', str(error.exception))
        self.assertEqual(get.call_args.kwargs['headers']['Authorization'], 'Bearer fixture-secret')
        self.assertFalse(get.call_args.kwargs['allow_redirects'])
        self.assertNotIn('token', get.call_args.kwargs['params'])

    def test_provider_integration_fetches_once_for_all_official_sources(self):
        sources = [{'id': 1, 'name': 'One', 'url': URL, 'official': 1},
                   {'id': 2, 'name': 'Two', 'url': 'https://facebook.com/other.page', 'official': 1}]
        with patch.dict(os.environ, {'FACEBOOK_READER_PROVIDER': 'apify'}), patch.object(reader.db, 'rows', return_value=sources), patch.object(apify, 'fetch', return_value=([], NOW.isoformat())) as fetch, patch.object(apify, 'page_posts', side_effect=[[dict(post(), id='123:1', published_at=NOW.isoformat())], SocialError('No identity')]), patch.object(reader, 'page_posts') as meta:
            result = reader.recent()
        fetch.assert_called_once()
        meta.assert_not_called()
        self.assertEqual(result['items'][0]['brotherhood'], 'One')
        self.assertEqual(result['pages'][1]['status'], 'unavailable')
        self.assertEqual(result['provider'], 'apify')


if __name__ == '__main__':
    unittest.main()
