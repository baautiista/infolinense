import base64
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image

_temp = tempfile.TemporaryDirectory()
os.environ.setdefault('DATA_DIR', _temp.name)

from app import canva, db, main, publishers, social_publish


class PublicationChoicesTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.enterContext(patch.object(db, 'DB_PATH', self.directory / 'desk.sqlite'))
        self.enterContext(patch.object(canva, 'RENDER_DIR', self.directory))
        self.enterContext(patch.object(main.brands, 'web_enabled', return_value=True))
        self.enterContext(patch.object(social_publish, 'connected_networks', return_value=['instagram', 'tiktok']))
        self.enterContext(patch.dict(main.app.dependency_overrides, {main.require_auth: lambda: None}))
        db.init_db()
        social_publish.init_tables()
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        candidate_id = db.exec_("INSERT INTO candidates(title,url) VALUES('Titular','manual:fixture')")
        self.photo = self.directory / 'automatic.jpg'
        Image.new('RGB', (40, 40), 'blue').save(self.photo)
        self.aid = db.exec_(
            """INSERT INTO articles(candidate_id,headline,subtitle,body,section,status,image_kind,image_local)
               VALUES(?,'Titular de la noticia','Subtítulo','Texto','OBRAS ','review_ready','web',?)""",
            (candidate_id, str(self.photo)),
        )
        self.path = f'/api/articles/{self.aid}'

    def test_refresh_and_save_use_canva_design_on_the_website_and_social(self):
        article = main.article(self.aid)
        db.exec_(
            "INSERT INTO canva_designs(article_id,design_id,url,content_hash) VALUES(?,'D1','url',?)",
            (self.aid, canva._content_hash(article)),
        )
        image = BytesIO()
        Image.new('RGB', (40, 40), 'red').save(image, 'PNG')
        with (
            patch.object(canva, 'ready', return_value=True),
            patch.object(canva, 'connected', return_value=True),
            patch.object(canva, '_page_count', return_value=1),
            patch.object(canva, '_api', return_value={'job': {'id': 'J'}}),
            patch.object(canva, '_wait', return_value={'urls': ['https://www.canva.com/export.png']}),
            patch.object(canva.requests, 'get', return_value=Mock(content=image.getvalue())),
        ):
            result = self.client.post(self.path + '/canva/export')
            self.assertEqual(result.status_code, 200, result.text)
        saved = self.client.put(self.path, json={'brand': 'infolinense', 'section': 'OBRAS', 'headline': 'Titular de la noticia\n'})
        render = self.directory / f'article_{self.aid}.png'
        self.assertEqual(saved.json()['render_path'], str(render))

        with (
            patch.object(publishers, 'publish', return_value='https://web.test/articulo') as web,
            patch.object(social_publish, 'publish', side_effect=lambda n, a: {'network': n, 'ok': True,
                         'path': social_publish.article_images(a)[0]}),
        ):
            result = self.client.post(self.path + '/publish', json={'networks': ['instagram'], 'publish_web': True})
        self.assertTrue(result.json()['published'])
        self.assertEqual(result.json()['results'][1]['path'], str(render))
        payload = publishers.web_payload(web.call_args.args[0])
        with Image.open(BytesIO(base64.b64decode(payload['image_base64']))) as cover:
            self.assertEqual(cover.format, 'JPEG')
            self.assertGreater(cover.getpixel((0, 0))[0], 240)
        self.assertEqual(publishers.website_cover(dict(main.article(self.aid), image_kind='web_photo')), self.photo)

    def test_unchecked_web_is_respected_for_immediate_and_scheduled_posts(self):
        db.exec_("UPDATE articles SET image_kind='user_upload' WHERE id=?", (self.aid,))
        targets = {'networks': ['instagram'], 'publish_web': False}
        at = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        self.assertEqual(self.client.post(self.path + '/schedule', json={**targets, 'at': at}).status_code, 200)
        self.assertEqual(main.article(self.aid)['scheduled_web'], 0)
        due = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        db.exec_('UPDATE articles SET scheduled_at=? WHERE id=?', (due, self.aid))
        with (
            patch.object(publishers, 'publish', side_effect=AssertionError('Web desmarcada')) as web,
            patch.object(social_publish, 'publish', side_effect=lambda n, a: {'network': n, 'ok': True}) as social,
        ):
            self.assertEqual(main.publish_due(), [self.aid])
            self.assertTrue(self.client.post(self.path + '/publish', json=targets).json()['published'])
            web.assert_not_called()
            self.assertEqual([call.args[0] for call in social.call_args_list], ['instagram', 'instagram'])
        self.assertEqual(self.client.post(self.path + '/publish', json={'networks': [], 'publish_web': False}).status_code, 400)
        with patch.object(publishers, 'publish', return_value='https://web.test/articulo') as web:
            result = self.client.post(self.path + '/publish', json={'networks': [], 'publish_web': True}).json()
            self.assertEqual(result['url'], 'https://web.test/articulo')
            web.assert_called_once()

    def test_republish_only_selected_destinations_and_safely_repeat_request(self):
        db.exec_("UPDATE articles SET status='published',image_kind='user_upload' WHERE id=?", (self.aid,))
        for name in ('instagram', 'tiktok'):
            social_publish._post_state(self.aid, name, 'published', name + '-previous')
        body = {'networks': ['instagram'], 'publish_web': False, 'request_id': str(uuid4())}

        def send(network, article):
            self.assertIsNone(db.row('SELECT * FROM social_posts WHERE article_id=? AND network=?', (self.aid, network)))
            social_publish._post_state(self.aid, network, 'published', network + '-new')
            return {'network': network, 'ok': True, 'message': 'Publicada'}

        with (
            patch.object(social_publish, 'publish', side_effect=send) as social,
            patch.object(publishers, 'publish') as web,
        ):
            first = self.client.post(self.path + '/republish', json=body)
            second = self.client.post(self.path + '/republish', json=body)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json(), second.json())
            social.assert_called_once()
            web.assert_not_called()
        previous = db.row('SELECT previous_posts FROM republications WHERE article_id=?', (self.aid,))
        self.assertEqual([item['remote_id'] for item in json.loads(previous['previous_posts'])], ['instagram-previous'])
        self.assertEqual(db.row("SELECT remote_id FROM social_posts WHERE article_id=? AND network='tiktok'", (self.aid,))['remote_id'], 'tiktok-previous')
