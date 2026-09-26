"""Regression checks for locality, freshness and bulletin extraction."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch

_temp = tempfile.TemporaryDirectory()
os.environ['DATA_DIR'] = _temp.name
os.environ['DB_PATH'] = str(Path(_temp.name) / 'radar.sqlite')
os.environ['RENDER_DIR'] = str(Path(_temp.name) / 'renders')
os.environ['UPLOAD_DIR'] = str(Path(_temp.name) / 'uploads')
os.environ['TEMPLATE_DIR'] = str(Path(_temp.name) / 'templates')

from app import db, sources, pipeline  # noqa: E402


class Response:
    def __init__(self, body):
        self.content = body.encode()
        self.text = body

    def raise_for_status(self):
        pass


class RadarChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def test_locality_and_age_keep_unrelated_entries_out(self):
        meta = {'priority': 70, 'official': 1, 'local_scope': 0}
        today = format_datetime(datetime.now(timezone.utc))
        old = format_datetime(datetime.now(timezone.utc) - timedelta(days=20))
        self.assertFalse(sources.exact_locality('La línea de metro abrirá un nuevo tramo'))
        self.assertFalse(sources.exact_locality('Los atletas llegan a la línea de salida'))
        self.assertFalse(sources.exact_locality('Viento de poniente en Cádiz'))
        self.assertTrue(sources.exact_locality('Obras de mejora en la Atunara'))
        self.assertIsNone(sources.add_candidate('La línea de metro abrirá un nuevo tramo', 'https://example.org/metro',
                                                  '', 'Boletín', published_at=today, source_meta=meta))
        self.assertIsNone(sources.add_candidate('Obras en La Línea de la Concepción', 'https://example.org/old',
                                                  '', 'Boletín', published_at=old, source_meta=meta))
        self.assertIsNotNone(sources.add_candidate('Obras en La Línea de la Concepción', 'https://example.org/new',
                                                     '', 'Boletín', published_at=today, source_meta=meta))

    def test_bop_reads_an_individual_notice_and_preserves_pdf_page(self):
        index = '<a href="/boletin/Boletin-numero-186-del-ano-2026">Boletín 186</a>'
        bulletin = '''<p><a href="/export/BOP186.pdf#page=5">304.477.- Ayuntamiento de Cádiz. Subasta local.</a></p>
                      <p><a href="/export/BOP186.pdf#page=6"><strong>305.221.- Ayuntamiento de La Línea de la Concepción.</strong>
                      Aprobación del presupuesto 2026.</a></p>'''
        def fake_fetch(url, timeout=18):
            return Response(index if url.endswith('/boletin/') else bulletin)
        with patch.object(sources, 'fetch', side_effect=fake_fetch):
            results = sources.parse_bop({'url': 'https://bopcadiz.es/boletin/'})
        self.assertEqual(len(results), 1)
        self.assertIn('305221', results[0]['url'])
        self.assertIn('#page=6&anuncio=', results[0]['url'])

    def test_social_source_uses_indexed_rss_instead_of_direct_scraping(self):
        xml = '''<rss><channel><item><title>Acto cultural en La Línea de la Concepción</title>
                 <link>https://news.google.com/example</link><pubDate>Fri, 25 Sep 2026 12:00:00 GMT</pubDate>
                 </item></channel></rss>'''
        called = []
        def fake_fetch(url, timeout=18):
            called.append(url)
            return Response(xml)
        with patch.object(sources, 'fetch', side_effect=fake_fetch):
            results = sources.read_source({'kind': 'social', 'url': 'https://www.facebook.com/aytolalinea'})
        self.assertEqual(len(results), 1)
        self.assertIn('news.google.com/rss/search', called[0])
        self.assertIn('facebook.com', called[0])

    def test_editor_controls_each_transition(self):
        cid = sources.add_candidate('La Línea mejora el parque Princesa Sofía',
                                    'https://example.org/parque', 'La fuente anuncia nuevas obras',
                                    'Fuente municipal', source_meta={'priority': 80, 'official': 1})
        with patch.object(sources, 'fetch_article_text', return_value='El Ayuntamiento anuncia mejoras en el parque.'), \
             patch.object(pipeline.photos, 'search_real_photos', return_value=[]), \
             patch.object(pipeline.renderer, 'render_article'):
            with self.assertRaises(ValueError): pipeline.draft_candidate(cid)
            research = pipeline.investigate_candidate(cid)
            self.assertEqual(db.row('SELECT status FROM candidates WHERE id=?', (cid,))['status'], 'researched')
            self.assertTrue(research['caveats'])  # Free mode identifies lack of independent verification.
            aid = pipeline.draft_candidate(cid)
            self.assertEqual(db.row('SELECT status FROM articles WHERE id=?', (aid,))['status'], 'draft')
            self.assertEqual(pipeline.submit_candidate(cid), aid)
            self.assertEqual(db.row('SELECT status FROM articles WHERE id=?', (aid,))['status'], 'review_ready')

    def test_procurement_rejects_old_tenders(self):
        today = int(datetime.now(timezone.utc).timestamp())
        old = today - 30 * 86400
        def row(number, stamp):
            return (f'<tr data-item-type="SearchTender"><td data-toggle-column-id="document_number">{number}</td>'
                    f'<td data-toggle-column-id="tender"><a href="/licitaciones/{number}">Obras en La Línea</a></td>'
                    f'<td data-toggle-column-id="call_for_tenders_published_at" data-sort-value="{stamp}"></td></tr>')
        with patch.object(sources, 'fetch', return_value=Response('<table>' + row('12', today) + row('11', old) + '</table>')):
            results = sources.parse_procurement({'url': 'https://contratos.gobierto.es/adjudicadores/linea'})
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]['url'].endswith('/licitaciones/12'))


if __name__ == '__main__':
    unittest.main()
