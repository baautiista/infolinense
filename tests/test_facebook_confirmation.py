"""Regresiones: no declarar éxito ni repetir una publicación no confirmada."""
import unittest
from unittest.mock import patch

from tests import test_social_publish as fixtures
from app import db, social_publish as sp, facebook_publishing as fb


class FacebookConfirmationTests(unittest.TestCase):
    setUp = fixtures.SocialPublishTests.setUp
    def fake(self, hidden=False, owner='PAGE', privacy='EVERYONE', member=True, scheduled=0):
        def graph(method, path, **kwargs):
            if path == '/me': return {'id': 'PAGE', 'is_published': True}
            if path.endswith('/published_posts'): return {'data': [{'id': 'PAGE_POST'}] if member else []}
            if method == 'POST' and path.endswith('/photos'): return {'id': 'ASSET'}
            if method == 'POST' and path.endswith('/feed'): return {'id': 'PAGE_POST'}
            return {'id': 'PAGE_POST', 'from': {'id': owner}, 'is_published': True,
                    'is_hidden': hidden, 'privacy': {'value': privacy},
                    'scheduled_publish_time': scheduled}
        return graph

    def test_unconfirmed_id_survives_and_retry_only_reads(self):
        with patch.object(sp, '_graph', side_effect=self.fake(hidden=True)) as graph:
            result = sp.publish('facebook', self.article)
            self.assertFalse(result['ok'])
            row = db.row("SELECT * FROM social_posts WHERE article_id=77 AND network='facebook'")
            self.assertEqual(row['remote_id'], 'PAGE_POST')
            self.assertEqual(row['status'], 'verification_pending')
            graph.reset_mock()
            sp.publish('facebook', self.article)
            self.assertTrue(all(call.args[0] == 'GET' for call in graph.call_args_list))

    def test_owner_privacy_schedule_and_membership_must_be_confirmed(self):
        for options in ({'owner': 'OTHER'}, {'privacy': 'SELF'}, {'scheduled': 2000000000}, {'member': False}):
            with self.subTest(options=options), patch.object(sp, '_graph', side_effect=self.fake(**options)):
                with self.assertRaises(sp.SocialError):
                    fb.verify(sp._meta(), 'PAGE_POST')

    def test_timeout_blocks_automatic_resend(self):
        def failing(method, path, **kw):
            if method == 'GET': return {'id': 'PAGE', 'is_published': True}
            raise sp.SocialError('Resultado desconocido')
        with patch.object(sp, '_graph', side_effect=failing):
            self.assertFalse(sp.publish('facebook', self.article)['ok'])
        with patch.object(sp, '_graph', side_effect=self.fake()) as graph:
            self.assertFalse(sp.publish('facebook', self.article)['ok'])
            self.assertTrue(all(call.args[0] == 'GET' for call in graph.call_args_list))

    def test_wrong_token_page_blocks_all_writes(self):
        with patch.object(sp, '_graph', return_value={'id': 'OTHER', 'is_published': True}) as graph:
            self.assertFalse(sp.publish('facebook', self.article)['ok'])
            self.assertEqual(graph.call_count, 1)
            self.assertIsNone(db.row("SELECT * FROM social_posts WHERE article_id=77 AND network='facebook'"))

    def test_text_link_and_multiple_images_explicitly_publish(self):
        for images, link in (([], None), ([], 'https://example.org/noticia'), ([self.card, self.card], None)):
            with self.subTest(images=len(images), link=link), patch.object(sp, '_graph', side_effect=self.fake()) as graph:
                remote, _ = sp._facebook_post('PAGE', 'TOK', 'Texto', images, link)
                self.assertEqual(remote, 'PAGE_POST')
                writes = [c for c in graph.call_args_list if c.args[0] == 'POST']
                self.assertEqual(writes[-1].kwargs['data']['published'], 'true')
                self.assertNotIn('scheduled_publish_time', writes[-1].kwargs['data'])
                if link:
                    self.assertEqual(writes[-1].kwargs['data']['link'], link)

    def test_missing_flags_do_not_count_as_public(self):
        with patch.object(sp, '_graph', side_effect=[{'data': [{'id': 'PAGE_POST'}]}, {'from': {'id': 'PAGE'}}]):
            with self.assertRaises(sp.SocialError):
                fb.verify(sp._meta(), 'PAGE_POST')

    def test_cofrade_canonical_page_must_match(self):
        with sp.use_brand('cofrade'):
            sp._save_account('meta', {'page': {'id': 'WRONG', 'access_token': 'TOK'}})
            with patch.object(sp, '_graph', side_effect=[{'id': 'WRONG', 'is_published': True}, {'id': 'COFRADE'}]) as graph:
                with self.assertRaises(sp.SocialError):
                    fb.page_identity()
                self.assertTrue(all(c.args[0] == 'GET' for c in graph.call_args_list))

    def test_permission_error_does_not_confirm_or_resend(self):
        db.exec_("INSERT INTO social_posts(article_id,network,status,remote_id) VALUES(77,'facebook','published','PAGE_POST')")
        def graph(method, path, **kwargs):
            if path == '/me': return {'id': 'PAGE', 'is_published': True}
            raise sp.SocialError('Permiso insuficiente')
        with patch.object(sp, '_graph', side_effect=graph) as request:
            self.assertFalse(sp.publish('facebook', self.article)['ok'])
            self.assertTrue(all(c.args[0] == 'GET' for c in request.call_args_list))
        self.assertEqual(db.row("SELECT remote_id FROM social_posts WHERE article_id=77")['remote_id'], 'PAGE_POST')

    def test_oauth_never_falls_back_to_an_unrelated_page(self):
        with sp.use_brand('cofrade'):
            self.assertIsNone(sp._page_for_brand([{'id': 'OTHER', 'name': 'Otra página', 'instagram_username': 'cofrade_aficionado'}]))
            self.assertEqual(sp._page_for_brand([{'id': 'COFRADE', 'name': 'El Cofrade Linense'}])['id'], 'COFRADE')

    def test_transport_error_never_exposes_token(self):
        import requests
        with patch.object(sp.requests, 'request', side_effect=requests.ConnectionError('https://example.org/?access_token=SECRET')):
            with self.assertRaises(sp.SocialError) as caught:
                sp._graph('GET', '/me', params={'access_token': 'SECRET'})
        self.assertNotIn('SECRET', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
