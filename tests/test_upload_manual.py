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


class FourLineTests(unittest.TestCase):
    def test_four_line_headline_is_kept(self):
        from app import ai
        h = 'El Ayuntamiento de La Línea abre el plazo de las becas de transporte'
        self.assertEqual(ai.headline_line_count(h, 'infolinense'), 4)
        with patch.object(ai, 'AI_ENABLED', True), patch.object(ai, 'ask_json', side_effect=AssertionError('no recortar')):
            self.assertEqual(ai.shorten_headline({'headline': h, 'brand': 'infolinense'}), h)


class HidePublishedTests(unittest.TestCase):
    def test_published_is_removed_from_panel(self):
        db.init_db()
        cid = db.exec_("INSERT INTO candidates(title,url,status) VALUES('x','https://x/h','published')")
        aid = db.exec_("INSERT INTO articles(candidate_id,headline,body,status) VALUES(?,?,?,?)", (cid, 'T', 'B', 'published'))
        with patch.object(main.social_publish, 'posts_for', return_value={}):
            self.assertIn(aid, [r['id'] for r in main.to_publish()])
            self.assertTrue(main.delete_article(aid)['hidden'])
            self.assertNotIn(aid, [r['id'] for r in main.to_publish()])
        self.assertEqual(db.row('SELECT status FROM articles WHERE id=?', (aid,))['status'], 'published')


class PlacspTests(unittest.TestCase):
    def test_only_la_linea_tenders(self):
        from app import sources
        db.init_db(); db.exec_('CREATE TABLE IF NOT EXISTS placsp_state(id TEXT PRIMARY KEY, estado TEXT, updated TEXT)'); db.exec_('DELETE FROM placsp_state')
        from datetime import date, timedelta
        d1, d2 = (date.today() - timedelta(days=1)).isoformat(), date.today().isoformat()
        feed = '''<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Suministro de luminarias LED</title><link href="https://contrataciondelestado.es/wps/poc?uri=deeplink:detalle_licitacion&amp;idEvl=AAA"/>
<summary type="text">Id licitación: 12/2026; Órgano de Contratación: Alcaldía del Ayuntamiento de La Línea de la Concepción; Importe: 120000 EUR; Estado: PUB</summary>
<updated>2026-10-07T10:00:00+02:00</updated></entry>
<entry><title>Obras en Madrid</title><link href="https://contrataciondelestado.es/x"/><summary>Órgano de Contratación: Ayuntamiento de Madrid; Estado: PUB</summary><updated>2026-10-07T10:00:00+02:00</updated></entry>
</feed>'''.replace('2026-10-07', d1)
        class R:
            content = feed.encode(); ok = True
            def raise_for_status(self): pass
        with patch.object(sources.requests, 'get', return_value=R()):
            items = sources.read_source({'url': sources.PLACSP_FEED, 'kind': 'procurement'})
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]['title'].startswith('Licitación: '))
        self.assertIn('idEvl=AAA', items[0]['url'])
        self.assertTrue(sources.tender_ok(items[0]['title'], items[0]['excerpt']))
        # la misma licitación pasa a adjudicada: aviso nuevo, con otra dirección
        feed2 = feed.replace('Estado: PUB', 'Estado: ADJ').replace(d1 + 'T10:00', d2 + 'T10:00')
        R.content = feed2.encode()
        with patch.object(sources.requests, 'get', return_value=R()):
            again = sources.read_source({'url': sources.PLACSP_FEED, 'kind': 'procurement'})
        self.assertEqual(len(again), 1)
        self.assertTrue(again[0]['title'].startswith('Adjudicada: '))
        self.assertNotEqual(again[0]['url'], items[0]['url'])
        with patch.object(sources.requests, 'get', return_value=R()):
            self.assertEqual(sources.read_source({'url': sources.PLACSP_FEED, 'kind': 'procurement'}), [])  # sin cambios


class FilterTests(unittest.TestCase):
    def test_bogota_from_gibraltar_query_is_rejected(self):
        from app import sources
        db.init_db()
        meta = {'priority': 70, 'official': 0, 'local_scope': 0, 'kind': 'rss'}
        cid = sources.add_candidate('Arrancan las obras en ALO Sur: transformará el acceso al centro de Bogotá',
                                    'https://bogota.gov.co/x', 'Bogota.gov.co', 'Gibraltar · rellenos, obras, eventos y elecciones',
                                    published_at='', source_meta=meta, outlet='Bogota.gov.co')
        self.assertIsNone(cid)

    def test_same_story_different_headlines(self):
        from app import sources
        self.assertTrue(sources.story_match(
            'Detenido en La Línea un hombre por robar en tres comercios de la calle Real', '',
            'La Policía Nacional detiene a un vecino de La Línea por robos en comercios de la calle Real', ''))
        self.assertFalse(sources.story_match('Abre la piscina municipal de La Línea', '', 'Corte de agua en el centro de La Línea', ''))


class ScanOrderTests(unittest.TestCase):
    def test_ayuntamiento_edictos_licitaciones_first(self):
        from app import sources
        db.init_db()
        order = []
        def fake(src):
            order.append(src['name']); return []
        with patch.object(sources, 'read_source', side_effect=fake), patch('app.ai.provider_chain', return_value=[]):
            sources.scan_all()
        first = [n for n in order[:6]]
        prio = [s['name'] for s in db.rows('SELECT * FROM sources WHERE active=1') if sources.is_priority(s)]
        self.assertTrue(prio)
        self.assertEqual(set(order[:len(prio)]), set(prio))
        self.assertFalse(sources._scan_state['busy'])
