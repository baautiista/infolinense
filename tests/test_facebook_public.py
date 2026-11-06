"""Synthetic HTML tests only. These fixtures do not validate live Facebook coverage."""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, Mock
from app import facebook_public as public, facebook_reader as reader
from app.social_publish import SocialError

NOW = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
URL = 'https://www.facebook.com/fixture.page'


def node(**changes):
    value = {'post_id': '456', 'creation_time': int((NOW-timedelta(days=1)).timestamp()),
             'comet_sections': {'content': {'story': {'actors': [{'__typename': 'Page', 'id': '123', 'url': URL, 'name': 'Fixture page'}],
                 'message': {'text': 'Fixture only'}, 'wwwURL': URL+'/posts/456'}}},
             'attachments': [{'media': {'photo_image': {'uri': 'https://example.org/post.jpg'}}}]}
    value.update(changes)
    return value


def html(*nodes):
    data = {'data': {'timeline_list_feed_units': {'edges': [{'node': n} for n in nodes]}}}
    return '<script type="application/json">'+json.dumps(data)+'</script>'


class PublicTests(unittest.TestCase):
    def setUp(self):
        setting = patch.object(reader.db, 'get_setting', return_value='')
        setting.start()
        self.addCleanup(setting.stop)

    def test_owned_post_photo_and_partial_coverage_notice(self):
        result = public.parse(html(node()), URL, NOW)
        self.assertTrue(result['partial'])
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['items'][0]['image'], 'https://example.org/post.jpg')
        self.assertEqual(result['items'][0]['page_id'], '123')
        self.assertIn('no', result['message'].lower())

    def test_facebook_user_backed_by_a_page_is_verified_by_id_not_name(self):
        story = {'actors': [{'__typename': 'User', 'id': '123', 'url': URL}], 'message': {'text': 'Fixture'}, 'wwwURL': URL+'/posts/456'}
        post = node(comet_sections={'content': {'story': story}})
        roots = [{'owner': {'id': '123', 'delegate_page': {'id': '789', 'category_name': 'Religious organization'}},
                  'timeline_list_feed_units': {'edges': [{'node': post}]}}]
        result = public.parse_payloads(roots, URL, NOW)
        self.assertEqual(result['items'][0]['page_id'], '123')
        roots[0]['owner']['id'] = '999'
        with self.assertRaises(SocialError):
            public.parse_payloads(roots, URL, NOW)

    def test_personal_profile_wrong_page_and_shared_posts_rejected(self):
        for author in [{'__typename': 'User', 'id': '123', 'url': URL},
                       {'__typename': 'Page', 'id': '999', 'url': 'https://facebook.com/other'}]:
            story = {'actors': [author], 'wwwURL': URL+'/posts/456'}
            with self.assertRaises(SocialError):
                public.parse(html(node(comet_sections={'content': {'story': story}})), URL, NOW)
        with self.assertRaises(SocialError):
            public.parse(html(node(attached_story={'message': {'text': 'Shared'}})), URL, NOW)

    def test_recent_only_deduplicated_and_future_excluded(self):
        old = node(post_id='old', creation_time=int((NOW-timedelta(days=8)).timestamp()))
        future = node(post_id='future', creation_time=int((NOW+timedelta(days=1)).timestamp()))
        result = public.parse(html(node(), node(), old, future), URL, NOW)
        self.assertEqual([p['id'] for p in result['items']], ['123:456'])

    def test_login_page_is_not_reported_as_no_posts(self):
        with self.assertRaisesRegex(SocialError, 'timeline público'):
            public.parse('<html>Log into Facebook</html>', URL, NOW)

    def test_no_paid_implicit_fallback_from_existing_apify_variables(self):
        with patch.dict(os.environ, {'FACEBOOK_READER_PROVIDER': 'apify', 'FACEBOOK_ALLOW_PAID': 'false', 'APIFY_TOKEN': 'fixture', 'APIFY_FACEBOOK_TASK_ID': 'task'}):
            self.assertEqual(reader.configuration()['provider'], 'public')

    def test_login_redirect_reports_access_barrier_not_empty_results(self):
        response = Mock(status_code=302, headers={'Location': 'https://www.facebook.com/login/?next=fixture'})
        with patch.object(public.requests, 'get', return_value=response) as get, self.assertRaisesRegex(SocialError, 'exige iniciar sesión'):
            public.page_posts(URL)
        self.assertEqual(get.call_count, 1)

    def test_public_request_never_follows_redirect_or_uses_credentials(self):
        import requests
        with patch.object(public.requests, 'get', side_effect=requests.ConnectionError('fixture')) as get, self.assertRaises(SocialError):
            public.page_posts(URL)
        self.assertFalse(get.call_args.kwargs['allow_redirects'])
        self.assertNotIn('Authorization', get.call_args.kwargs['headers'])
        self.assertNotIn('cookies', get.call_args.kwargs)


if __name__ == '__main__':
    unittest.main()
