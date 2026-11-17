import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

_temp = tempfile.TemporaryDirectory()
os.environ.setdefault('DATA_DIR', _temp.name)

from fastapi.testclient import TestClient

from app import chatgpt_import, db, main


class ChatGPTImportTests(TestCase):
    def setUp(self):
        self.database = tempfile.TemporaryDirectory()
        self.addCleanup(self.database.cleanup)
        config = patch.object(db, 'DB_PATH', Path(self.database.name) / 'desk.sqlite')
        config.start()
        self.addCleanup(config.stop)
        db.init_db()
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.path = '/api/import/chatgpt/draft'
        self.draft = {
            'headline': 'La Atunara amplía su oferta de formación con dos nuevas aulas',
            'subtitle': 'El proyecto incorpora un espacio para cursos de informática.',
            'body': 'El Ayuntamiento proyecta dos aulas.\n\nTambién prevé una sala de informática.',
        }

    def authorize(self):
        main.app.dependency_overrides[chatgpt_import.require_auth] = lambda: None
        self.addCleanup(main.app.dependency_overrides.pop, chatgpt_import.require_auth)

    def test_session_required_before_writing(self):
        with patch('app.auth.JWT_SECRET', 'test-secret'):
            self.assertEqual(self.client.post(self.path, json=self.draft).status_code, 401)
        self.assertEqual(db.row('SELECT count(*) n FROM articles')['n'], 0)
        page = self.client.get('/enviar-a-desk')
        self.assertEqual(page.status_code, 200)
        self.assertIn("frame-ancestors 'none'", page.headers['content-security-policy'])

    def test_same_link_saves_once_and_reuses_canva(self):
        self.authorize()
        article_id = self.client.post(self.path, json=self.draft).json()['id']
        self.assertEqual(self.client.post(self.path, json=self.draft).json()['id'], article_id)
        self.assertEqual(db.row('SELECT count(*) n FROM candidates')['n'], 1)
        row = db.row('SELECT * FROM articles WHERE id=?', (article_id,))
        self.assertEqual(row['status'], 'review_ready')
        self.assertIsNone(row['scheduled_at'])
        self.assertEqual(row['subtitle'], self.draft['subtitle'])
        self.assertEqual(row['body'], self.draft['body'])
        review = self.client.get('/api/drafting').json()
        self.assertTrue(any(item['article_id'] == article_id and item['state'] == 'ready' for item in review))

        path = f'{self.path}/{article_id}/canva'
        with (
            patch('app.canva.ready', return_value=True),
            patch('app.canva.connected', return_value=True),
            patch('app.canva._autofill_text_only', return_value=('DA123', 1)) as autofill,
            patch('app.canva._keep_only_page'),
        ):
            result = self.client.post(path)
            self.assertEqual(result.json(), {'url': 'https://www.canva.com/design/DA123/edit', 'needs_photo': True})
            self.assertEqual(self.client.post(path).json(), result.json())
            autofill.assert_called_once()
        self.assertEqual(db.row('SELECT exported FROM canva_designs WHERE article_id=?', (article_id,))['exported'], 0)

    def test_existing_article_is_updated_and_scheduled_article_is_protected(self):
        self.authorize()
        article_id = self.client.post(self.path, json=self.draft).json()['id']
        edited = {**self.draft, 'article_id': article_id, 'subtitle': 'La sala tendrá capacidad para 30 personas.'}
        self.assertEqual(self.client.post(self.path, json=edited).json()['id'], article_id)
        self.assertEqual(db.row('SELECT subtitle FROM articles WHERE id=?', (article_id,))['subtitle'], edited['subtitle'])
        db.exec_("UPDATE articles SET scheduled_at='2026-12-01T18:00:00+01:00' WHERE id=?", (article_id,))
        self.assertEqual(self.client.post(self.path, json=edited).status_code, 409)

    def test_canva_failure_keeps_draft_available(self):
        self.authorize()
        article_id = self.client.post(self.path, json=self.draft).json()['id']
        with patch('app.canva.create_text_design', side_effect=ValueError('Canva no está conectado')):
            result = self.client.post(f'{self.path}/{article_id}/canva')
        self.assertEqual(result.status_code, 502)
        self.assertEqual(db.row('SELECT status FROM articles WHERE id=?', (article_id,))['status'], 'review_ready')
