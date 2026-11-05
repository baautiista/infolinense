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
    def test_page_posts_for_uses_public_app_token_for_unmanaged_page(self):
        calls = []

        def fake_graph(method, path, **kwargs):
            token = kwargs.get('params', {}).get('access_token')
            calls.append((path, token))
            if path == '/gran-poder-linense':
                return {'id': 'PAGE-123', 'name': 'Gran Poder y Ángeles'}
            if path == '/PAGE-123/posts':
                return {'data': [{'message': 'Misa a las 19 h', 'created_time': '2026-11-04T18:00:00+0000',
                                  'permalink_url': 'https://www.facebook.com/gran-poder-linense/posts/123'}]}
            raise AssertionError(path)

        with patch.object(sp, 'META_APP_ID', 'APP-ID'), patch.object(sp, 'META_APP_SECRET', 'APP-SECRET'), \
             patch.object(sp, 'META_APP_ACCESS_TOKEN', ''), patch.object(sp, '_managed_pages', return_value=[]), \
             patch.object(sp, '_graph', side_effect=fake_graph):
            posts = sp.page_posts_for('https://www.facebook.com/gran-poder-linense', 'Gran Poder y Ángeles')

        self.assertEqual(posts[0]['title'], 'Misa a las 19 h')
        self.assertEqual(posts[0]['published_at'], '2026-11-04T18:00:00+0000')
        self.assertEqual(calls, [('/gran-poder-linense', 'APP-ID|APP-SECRET'),
                                  ('/PAGE-123/posts', 'APP-ID|APP-SECRET')])

    def setUp(self):
        db.init_db(); sp.init_tables()
        for t in ('social_posts', 'social_accounts', 'public_media', 'carousel_designs', 'article_carousel_uploads', 'scheduled_stories'):
            db.exec_('DELETE FROM ' + t)
        self.card = RENDER_DIR / 'article_77.png'
        Image.new('RGB', (1080, 1350), 'blue').save(self.card)
        self.article = {'id': 77, 'headline': '19 patinetes retirados', 'subtitle': 'La Policía Local actúa.',
                        'body': 'Texto de la noticia. ' * 50, 'render_path': str(self.card)}
        sp.PUBLIC_BASE_URL = 'https://desk.example.com'
        sp._save_account('meta', {'pages': [], 'page': {'id': 'PAGE', 'name': 'InfoLinense', 'access_token': 'TOK',
                                                          'instagram_id': 'IG', 'instagram_username': 'infolinense'}})

    def test_expired_public_media_is_removed_without_touching_current_images(self):
        import time
        old = RENDER_DIR / 'public_expired.jpg'
        current = RENDER_DIR / 'public_current.jpg'
        old.write_bytes(b'old')
        current.write_bytes(b'current')
        timestamp = time.time()
        os.utime(old, (timestamp - 9 * 86400, timestamp - 9 * 86400))
        try:
            db.exec_('INSERT INTO public_media VALUES(?,?,?)', ('expired', str(old), int(timestamp - 86400)))
            db.exec_('INSERT INTO public_media VALUES(?,?,?)', ('current', str(current), int(timestamp + 86400)))
            sp.cleanup_expired_public_media(timestamp)
            self.assertFalse(old.exists())
            self.assertTrue(current.exists())
            self.assertTrue(self.card.exists())
            self.assertIsNone(db.row('SELECT token FROM public_media WHERE token=?', ('expired',)))
            self.assertIsNotNone(db.row('SELECT token FROM public_media WHERE token=?', ('current',)))
        finally:
            current.unlink(missing_ok=True)

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

    def test_uploaded_carousel_returns_twenty_images_in_order(self):
        paths = []
        for i in range(1, 21):
            path = RENDER_DIR / ('carousel-upload-%02d.jpg' % i)
            Image.new('RGB', (20, 20), (i, 0, 0)).save(path, 'JPEG')
            paths.append(str(path))
            db.exec_('INSERT INTO article_carousel_uploads(article_id,slide_index,path) VALUES(?,?,?)',
                     (self.article['id'], i, str(path)))
        try:
            self.assertEqual(sp.article_images(self.article), paths)
        finally:
            for path in paths:
                Path(path).unlink(missing_ok=True)

    def test_buffer_instagram_carousel_accepts_twenty_photos(self):
        calls = []
        def fake_query(token, query, variables=None):
            calls.append((token, query, variables))
            return {'createPost': {'post': {'id': 'IG_POST', 'status': 'sent', 'externalLink': 'https://instagram.com/p/post'}}}
        with patch.object(sp, '_buffer_instagram_account', return_value={'api_key': 'KEY', 'channel_id': 'IG_CHANNEL'}), \
             patch.object(sp, 'public_image', side_effect=['https://desk.example.com/p/%s.jpg' % i for i in range(20)]), \
             patch.object(sp, '_buffer_query', side_effect=fake_query):
            result = sp._buffer_instagram_post(self.article, [str(self.card)] * 20)
        self.assertEqual(result, ('IG_POST', 'https://instagram.com/p/post', 'sent'))
        request = calls[0][2]['input']
        self.assertEqual(len(request['assets']), 20)
        self.assertEqual(request['metadata']['instagram']['type'], 'post')

    def test_buffer_instagram_story_uses_story_metadata(self):
        calls = []
        def fake_query(token, query, variables=None):
            calls.append((token, query, variables))
            return {'createPost': {'post': {'id': 'STORY_1', 'status': 'scheduled', 'externalLink': ''}}}
        with patch.object(sp, '_buffer_instagram_account', return_value={'api_key': 'KEY', 'channel_id': 'IG_CHANNEL'}), \
             patch.object(sp, 'public_image', return_value='https://desk.example.com/p/story.jpg'), \
             patch.object(sp, '_buffer_query', side_effect=fake_query):
            result = sp._buffer_instagram_story(self.article, str(self.card))
        self.assertEqual(result, ('STORY_1', '', 'scheduled'))
        request = calls[0][2]['input']
        self.assertEqual(request['metadata']['instagram']['type'], 'story')
        self.assertEqual(request['assets'][0]['image']['url'], 'https://desk.example.com/p/story.jpg')

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
        self.assertNotIn('19 patinetes', calls[0][2]['message']); self.assertIn('Texto de la noticia', calls[0][2]['message'])

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

    def test_tiktok_buffer_connection_and_confirmation_without_duplicates(self):
        calls = []
        def fake(url, json=None, **kw):
            calls.append(json)
            query = json['query']
            if 'organizations' in query:
                return Resp({'data': {'account': {'organizations': [{'id': 'O1'}]}}})
            if 'channels(input:' in query:
                return Resp({'data': {'channels': [{'id': 'C1', 'displayName': 'InfoLinense', 'service': 'tiktok'}]}})
            if 'createPost' in query:
                return Resp({'data': {'createPost': {'post': {'id': 'B1', 'status': 'sending', 'externalLink': None}}}})
            if 'post(input:' in query:
                return Resp({'data': {'post': {'status': 'sent', 'externalLink': 'https://www.tiktok.com/@infolinense/photo/1'}}})
            raise AssertionError(query)
        with patch.object(sp.requests, 'post', side_effect=fake):
            self.assertEqual(sp.connect_buffer_tiktok('my-key')['name'], 'InfoLinense')
            self.assertTrue(sp.status()['tiktok']['connected'])
            r = sp.publish('tiktok', self.article)
            self.assertTrue(r['ok']); self.assertIn('pendiente', r['message'])
            self.assertEqual(sp.publish('tiktok', self.article)['message'], r['message'])
            mutation = next(c['variables']['input'] for c in calls if 'createPost' in c['query'])
            self.assertEqual(mutation['mode'], 'shareNow')
            self.assertEqual(mutation['schedulingType'], 'automatic')
            self.assertEqual(mutation['metadata']['tiktok']['title'], self.article['headline'])
            self.assertTrue(mutation['assets'][0]['image']['url'].startswith('https://desk.example.com/p/'))
            db.exec_("UPDATE social_posts SET updated_at=datetime('now','-3 minutes') WHERE article_id=77 AND network='tiktok'")
            sp.refresh_buffer_posts()
        post = db.row("SELECT status,url FROM social_posts WHERE article_id=77 AND network='tiktok'")
        self.assertEqual(post['status'], 'published')
        self.assertEqual(post['url'], 'https://www.tiktok.com/@infolinense/photo/1')

    def test_tiktok_rejected_direct_oauth_never_claims_connected(self):
        sp._save_account('tiktok', {'access_token': 'T', 'refresh_token': 'R', 'expires_at': 9e12})
        self.assertFalse(sp.status()['tiktok']['connected'])
        r = sp.publish('tiktok', self.article)
        self.assertFalse(r['ok']); self.assertIn('Buffer', r['message'])

    def test_buffer_rejects_other_channels_and_publication_error(self):
        def fake(url, json=None, **kw):
            if 'organizations' in json['query']:
                return Resp({'data': {'account': {'organizations': [{'id': 'O1'}]}}})
            if 'channels(input:' in json['query']:
                return Resp({'data': {'channels': [
                    {'id': 'IG1', 'displayName': 'InfoLinense IG', 'service': 'instagram'},
                    {'id': 'TT1', 'displayName': 'InfoLinense', 'service': 'tiktok'}]}})
            return Resp({'data': {'createPost': {'message': 'Media format rejected'}}})
        with patch.object(sp.requests, 'post', side_effect=fake):
            with self.assertRaises(sp.SocialError):
                sp.connect_buffer_tiktok('key', 'IG1')
            sp.connect_buffer_tiktok('key')
            result = sp.publish('tiktok', self.article)
        self.assertFalse(result['ok'])
        self.assertIn('Media format rejected', result['message'])
        self.assertEqual(db.row("SELECT status FROM social_posts WHERE article_id=77 AND network='tiktok'")['status'], 'error')

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
