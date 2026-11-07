import json
import unittest
from unittest.mock import patch
from tests import test_social_publish as fixtures  # Inicializa una base temporal antes de importar la app.
from app import facebook_diagnostics as diagnostics, social_publish as sp, main


class DiagnosticTests(unittest.TestCase):
    def test_token_and_post_app_are_separate_and_secrets_never_returned(self):
        calls = []
        def graph(method, path, **kw):
            calls.append((method, path))
            if path == '/debug_token':
                return {'data': {'app_id': 'OTHER_APP', 'application': 'Old app', 'access_token': 'SECRET', 'is_valid': True}}
            if path == '/me':
                return {'id': 'PAGE', 'name': 'El Cofrade Linense', 'is_published': True, 'access_token': 'SECRET'}
            if path.endswith('/settings'):
                return {'data': [{'setting': 'AGE_RESTRICTIONS', 'value': {'age': '18+'}}, {'setting': 'PRIVATE_SETTING', 'value': 'SECRET'}]}
            if path.endswith('/published_posts'):
                return {'data': [{'id': 'PAGE_POST'}]}
            if path == '/123':
                return {'id': '123', 'from': {'id': 'PAGE'}, 'page_story_id': 'PAGE_POST'}
            field = kw['params']['fields']
            return {field: {'application': {'id': 'POST_APP', 'name': 'Posting app', 'access_token': 'SECRET'},
                            'is_published': True, 'is_hidden': False, 'privacy': {'value': 'EVERYONE'}}.get(field)}
        with patch.object(sp, '_meta', return_value={'id': 'PAGE', 'access_token': 'SECRET'}), patch.object(sp, '_graph', side_effect=graph), patch.object(sp, 'META_APP_ID', 'APP'), patch.object(sp, 'META_APP_SECRET', 'APP_SECRET'):
            report = diagnostics.diagnose(['123'])
        self.assertFalse(report['configured_app_matches'])
        self.assertTrue(report['token_page_matches'])
        self.assertEqual(report['photos'][0]['post']['application']['id'], 'POST_APP')
        self.assertTrue(report['photos'][0]['published_posts_found'])
        self.assertEqual(report['audience_settings'][0]['value']['age'], '18+')
        self.assertNotIn('SECRET', json.dumps(report))
        self.assertTrue(all(method == 'GET' for method, _ in calls))

    def test_one_unsupported_field_does_not_hide_other_evidence(self):
        def graph(method, path, **kw):
            if path == '/me': return {'id': 'PAGE'}
            if path == '/123': return {'id': '123', 'page_story_id': 'PAGE_POST'}
            if path == '/PAGE_POST':
                if kw['params']['fields'] == 'is_hidden': return {'is_hidden': True}
                raise sp.SocialError('Unsupported field')
            raise sp.SocialError('Not available')
        with patch.object(sp, '_meta', return_value={'id': 'PAGE', 'access_token': 'SECRET'}), patch.object(sp, '_graph', side_effect=graph), patch.object(sp, 'META_APP_ID', ''):
            report = diagnostics.diagnose(['123'])
        self.assertTrue(report['photos'][0]['post']['is_hidden'])
        self.assertGreater(len(report['unknown']), 0)
        self.assertNotIn('is_published', report['photos'][0]['post'])

    def test_no_connection_is_explicit(self):
        with patch.object(sp, '_meta', return_value={}):
            self.assertIn('error', diagnostics.diagnose())

    def test_endpoint_rejects_paths_and_excessive_ids_before_querying_meta(self):
        from fastapi import HTTPException
        for ids in ('../me', '123,456,789', '123?access_token=SECRET'):
            with self.subTest(ids=ids), self.assertRaises(HTTPException), patch.object(sp, '_graph', side_effect=AssertionError('No query')):
                main.facebook_diagnose(photo_ids=ids)
