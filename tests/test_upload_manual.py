import asyncio
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

_temp = tempfile.TemporaryDirectory()
os.environ.setdefault('DATA_DIR', _temp.name)
os.environ.setdefault('DB_PATH', str(Path(_temp.name) / 'manual.sqlite'))
for _k, _v in (('RENDER_DIR', 'renders'), ('UPLOAD_DIR', 'uploads'), ('TEMPLATE_DIR', 'templates')):
    os.environ.setdefault(_k, str(Path(_temp.name) / _v))
    Path(os.environ[_k]).mkdir(parents=True, exist_ok=True)

from PIL import Image  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from app import db, main  # noqa: E402


class FakeUpload:
    def __init__(self, name, data): self.filename, self._d = name, data
    async def read(self, n=-1): return self._d


def call(**kw):
    args = dict(text='', title='', link='', brand='infolinense', priority='today', files=[])
    args.update(kw)
    return asyncio.run(main.manual_news(**args))


class ManualUploadTests(unittest.TestCase):
    def setUp(self):
        db.init_db(); db.exec_('DELETE FROM candidates')
        self.p1 = patch.object(main.pipeline, 'queue_auto_write'); self.q = self.p1.start()
        self.p2 = patch.object(main.planner, 'rebuild_schedule'); self.p2.start()

    def tearDown(self): self.p1.stop(); self.p2.stop()

    def test_text_creates_candidate_and_starts_writing(self):
        r = call(text='El Ayuntamiento de La Línea abre el plazo para las becas de transporte universitario hasta el 30 de octubre.',
                 brand='cofrade', priority='tomorrow')
        c = db.row('SELECT * FROM candidates WHERE id=?', (r['id'],))
        self.assertTrue(c['url'].startswith('manual:'))
        self.assertEqual((c['brand'], c['editorial_priority'], c['source_name']), ('cofrade', 'this_week', 'Redacción'))
        self.assertTrue(c['plan_day'])
        self.q.assert_called_once_with(r['id'], 'this_week')

    def test_photo_is_kept_and_short_text_rejected(self):
        buf = BytesIO(); Image.new('RGB', (1200, 1500), 'red').save(buf, 'PNG')
        r = call(text='Texto suficiente para redactar una noticia completa sobre la feria.', files=[FakeUpload('foto.png', buf.getvalue())])
        self.assertTrue(r['photo'])
        self.assertTrue(Path(db.row('SELECT manual_photo FROM candidates WHERE id=?', (r['id'],))['manual_photo']).exists())
        with self.assertRaises(HTTPException): call(text='corto')
        with self.assertRaises(HTTPException): call(text='x' * 50, files=[FakeUpload('virus.exe', b'MZ' + b'0' * 100)])

    def test_pdf_text_is_extracted(self):
        try:
            from pypdf import PdfWriter
        except ImportError:
            self.skipTest('pypdf no instalado')
        w = PdfWriter(); w.add_blank_page(200, 200); buf = BytesIO(); w.write(buf)
        with self.assertRaises(HTTPException):  # PDF sin texto y sin nada pegado
            call(files=[FakeUpload('bando.pdf', buf.getvalue())])
        r = call(text='Bando municipal sobre el corte de agua en el barrio de San Bernardo el jueves.', files=[FakeUpload('bando.pdf', buf.getvalue())])
        self.assertGreater(r['chars'], 40)


if __name__ == '__main__':
    unittest.main()


class HeadlineLengthTests(unittest.TestCase):
    def test_short_headline_is_completed_to_three_lines(self):
        from app import ai
        long = 'Abre el plazo de las ayudas al alquiler hasta el 30 de octubre'
        with patch.object(ai, 'AI_ENABLED', True), patch.object(ai, 'ask_json', return_value=({'headline': long}, 'gemini', False)):
            got = ai.shorten_headline({'headline': 'Ayudas al alquiler', 'subtitle': '', 'body': 'x', 'brand': 'infolinense'})
        self.assertEqual(ai.headline_line_count(got, 'infolinense'), 3)

    def test_three_line_headline_untouched(self):
        from app import ai
        h = 'Abre el plazo de las ayudas al alquiler hasta el 30 de octubre'
        with patch.object(ai, 'AI_ENABLED', True), patch.object(ai, 'ask_json', side_effect=AssertionError('no')):
            self.assertEqual(ai.shorten_headline({'headline': h, 'brand': 'infolinense'}), h)


class CofradePublishTests(unittest.TestCase):
    def test_cofrade_without_networks_says_why(self):
        db.init_db()
        cid = db.exec_("INSERT INTO candidates(title,url,status) VALUES('x','https://x/c','review_ready')")
        aid = db.exec_("INSERT INTO articles(candidate_id,headline,body,status,brand) VALUES(?,?,?,?,?)", (cid, 'T', 'B', 'review_ready', 'cofrade'))
        with patch.object(main.social_publish, 'connected_networks', return_value=[]):
            with self.assertRaises(HTTPException) as e:
                main.publish(aid, main.PublishIn(networks=['instagram', 'facebook']))
        self.assertIn('Conectar', e.exception.detail)

    def test_page_matched_by_brand_name(self):
        from app import social_publish as sp
        pages = [{'id': '1', 'name': 'InfoLinense', 'instagram_username': 'infolinense'},
                 {'id': '2', 'name': 'El Cofrade Linense', 'instagram_username': 'elcofradelinense'}]
        with sp.use_brand('cofrade'):
            self.assertEqual(sp._page_for_brand(pages)['id'], '2')
