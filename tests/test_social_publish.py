import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_temp = tempfile.TemporaryDirectory()
os.environ.setdefault('DATA_DIR', _temp.name)
os.environ.setdefault('DB_PATH', str(Path(_temp.name) / 'social.sqlite'))
os.environ.setdefault('RENDER_DIR', str(Path(_temp.name) / 'renders'))
os.environ.setdefault('UPLOAD_DIR', str(Path(_temp.name) / 'uploads'))
os.environ.setdefault('TEMPLATE_DIR', str(Path(_temp.name) / 'templates'))

from PIL import Image  # noqa: E402
from app import db, main, social_publish as sp  # noqa: E402
from app.config import RENDER_DIR  # noqa: E402


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.ok = data, status, status < 400

    def json(self):
        return self._d


class SocialPublishTests(unittest.TestCase):
    def setUp(self):
        db.init_db(); sp.init_tables()
        for t in ('social_posts', 'social_accounts', 'public_media'):
            db.exec_('DELETE FROM ' + t)
        self.card = RENDER_DIR / 'article_77.png'
        Image.new('RGB', (1080, 1350), 'blue').save(self.card)
        self.article = {'id': 77, 'headline': '19 patinetes retirados', 'subtitle': 'La Policía Local actúa.',
                        'body': 'Texto de la noticia. ' * 50, 'render_path': str(self.card)}
        sp.PUBLIC_BASE_URL = 'https://desk.example.com'
        sp._save_account('meta', {'pages': [], 'page': {'id': 'PAGE', 'name': 'InfoLinense', 'access_token': 'TOK',
                                                          'instagram_id': 'IG', 'instagram_username': 'infolinense'}})

    def test_caption_respects_instagram_limit(self):
        long = dict(self.article, body='Frase larga de la noticia. ' * 200)
        text = sp.caption(long, 2200)
        self.assertLessEqual(len(text), 2200)
        self.assertTrue(text.endswith(sp.SOCIAL_HASHTAGS))

    def test_public_image_is_jpeg_and_served(self):
        url = sp.public_image(str(self.card))
        self.assertTrue(url.startswith('https://desk.example.com/p/') and url.endswith('.jpg'))
        token = url.rsplit('/', 1)[1][:-4]
        self.assertEqual(Image.open(sp.public_file(token)).format, 'JPEG')
        self.assertIsNone(sp.public_file('inventado'))

    def test_facebook_single_photo(self):
        calls = []
        def fake(method, url, **kw):
            calls.append((method, url, kw.get('data') or kw.get('params')))
            return Resp({'id': '1', 'post_id': 'PAGE_9'})
        with patch.object(sp.requests, 'request', side_effect=fake):
            r = sp.publish('facebook', self.article)
        self.assertTrue(r['ok'])
        self.assertEqual(r['url'], 'https://www.facebook.com/PAGE_9')
        self.assertTrue(calls[0][1].endswith('/PAGE/photos'))
        self.assertIn('19 patinetes', calls[0][2]['message'])

    def test_instagram_waits_and_publishes_once(self):
        def fake(method, url, **kw):
            if url.endswith('/IG/media'): return Resp({'id': 'C1'})
            if url.endswith('/C1'): return Resp({'status_code': 'FINISHED'})
            if url.endswith('/IG/media_publish'): return Resp({'id': 'M1'})
            if url.endswith('/M1'): return Resp({'permalink': 'https://instagram.com/p/x'})
            return Resp({'error': {'message': 'inesperado'}}, 400)
        with patch.object(sp.requests, 'request', side_effect=fake):
            r = sp.publish('instagram', self.article)
        self.assertTrue(r['ok']); self.assertEqual(r['url'], 'https://instagram.com/p/x')
        with patch.object(sp.requests, 'request', side_effect=AssertionError('no debe repetir')):
            again = sp.publish('instagram', self.article)
        self.assertEqual(again['message'], 'Ya estaba publicada')

    def test_meta_error_is_reported_not_raised(self):
        with patch.object(sp.requests, 'request', return_value=Resp({'error': {'message': 'Token caducado'}}, 400)):
            r = sp.publish('facebook', self.article)
        self.assertFalse(r['ok']); self.assertIn('Token caducado', r['message'])

    def test_tiktok_uses_allowed_privacy(self):
        sp._save_account('tiktok', {'access_token': 'T', 'refresh_token': 'R', 'expires_at': 9e12})
        bodies = []
        def fake(url, json=None, **kw):
            bodies.append((url, json))
            if url.endswith('creator_info/query/'):
                return Resp({'data': {'privacy_level_options': ['SELF_ONLY']}, 'error': {'code': 'ok'}})
            return Resp({'data': {'publish_id': 'P1'}, 'error': {'code': 'ok'}})
        with patch.object(sp.requests, 'post', side_effect=fake):
            r = sp.publish('tiktok', self.article)
        self.assertTrue(r['ok']); self.assertIn('privada', r['message'])
        init = bodies[-1][1]
        self.assertEqual(init['media_type'], 'PHOTO')
        self.assertEqual(init['post_info']['privacy_level'], 'SELF_ONLY')
        self.assertTrue(init['source_info']['photo_images'][0].startswith('https://desk.example.com/p/'))

    def test_missing_canva_image(self):
        r = sp.publish('facebook', dict(self.article, id=78, render_path=''))
        self.assertFalse(r['ok']); self.assertIn('Canva', r['message'])

    def test_pasted_user_token_connects_page(self):
        sp.META_APP_ID, sp.META_APP_SECRET = '1', 's'
        def fake(method, url, **kw):
            if url.endswith('/oauth/access_token'): return Resp({'access_token': 'LONGUSER'})
            if url.endswith('/me/accounts'): return Resp({'data': [{'id': 'P9', 'name': 'InfoLinense', 'access_token': 'PT',
                                                                   'instagram_business_account': {'id': 'IG9', 'username': 'infolinense'}}]})
            return Resp({'error': {'message': 'x'}}, 400)
        with patch.object(sp.requests, 'request', side_effect=fake):
            page = sp.meta_from_user_token('EAAB' + 'x' * 40)
        self.assertEqual((page['id'], page['instagram_id']), ('P9', 'IG9'))
        self.assertTrue(sp.status()['instagram']['connected'])

    def test_instagram_direct_login_without_facebook_page(self):
        db.exec_("DELETE FROM social_accounts WHERE network='meta'")
        sp.INSTAGRAM_APP_ID, sp.INSTAGRAM_APP_SECRET = '55', 'sec'
        url = sp.instagram_login_url()
        self.assertIn('instagram.com/oauth/authorize', url)
        self.assertIn('instagram_business_content_publish', url)
        state = url.split('state=')[1].split('&')[0]
        def fake_post(url, data=None, **kw):
            self.assertEqual(data['grant_type'], 'authorization_code')
            return Resp({'access_token': 'SHORT', 'user_id': 123})
        def fake(method, url, **kw):
            if url.endswith('/access_token'): return Resp({'access_token': 'LONGIG', 'expires_in': 5184000})
            if url.endswith('/me'): return Resp({'user_id': '178', 'username': 'infolinense'})
            return Resp({'error': {'message': 'x'}}, 400)
        with patch.object(sp.requests, 'post', side_effect=fake_post), patch.object(sp.requests, 'request', side_effect=fake):
            sp.instagram_complete('CODE#_', state)
        st = sp.status()
        self.assertTrue(st['instagram']['connected'] and st['instagram']['direct'])
        self.assertFalse(st['facebook']['connected'])
        self.assertEqual(sp.connected_networks(), ['instagram'])
        self.assertNotIn('LONGIG', str(st))
        hosts = []
        def fake_pub(method, url, **kw):
            hosts.append(url)
            if url.endswith('/178/media'): return Resp({'id': 'C1'})
            if url.endswith('/C1'): return Resp({'status_code': 'FINISHED'})
            if url.endswith('/178/media_publish'): return Resp({'id': 'M1'})
            if url.endswith('/M1'): return Resp({'permalink': 'https://instagram.com/p/y'})
            return Resp({'error': {'message': 'inesperado'}}, 400)
        with patch.object(sp.requests, 'request', side_effect=fake_pub):
            r = sp.publish('instagram', dict(self.article, id=79))
        self.assertTrue(r['ok'], r)
        self.assertTrue(all(h.startswith('https://graph.instagram.com/') for h in hosts))

    def test_status_never_exposes_tokens(self):
        self.assertNotIn('TOK', str(sp.status()))


if __name__ == '__main__':
    unittest.main()
