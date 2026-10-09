"""Regression checks for locality, freshness and bulletin extraction."""
import os
import json
import io
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch
from PIL import Image

_temp = tempfile.TemporaryDirectory()
os.environ['AUTO_DRAFT_USEFUL'] = 'false'
os.environ['DATA_DIR'] = _temp.name
os.environ['DB_PATH'] = str(Path(_temp.name) / 'radar.sqlite')
os.environ['RENDER_DIR'] = str(Path(_temp.name) / 'renders')
os.environ['UPLOAD_DIR'] = str(Path(_temp.name) / 'uploads')
os.environ['TEMPLATE_DIR'] = str(Path(_temp.name) / 'templates')

from app import ai, db, sources, pipeline, canva, main  # noqa: E402
from fastapi import HTTPException  # noqa: E402


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

    def test_headline_size_variants_follow_editor_choice(self):
        schema = {'HEADLINE_L': {'type': 'text'}, 'HEADLINE_S': {'type': 'text'}}
        texts = {'HEADLINE': 'El puerto abre una nueva conexión'}
        large = canva.size_variants(schema, texts, 'large')
        small = canva.size_variants(schema, texts, 'small')
        self.assertEqual(large['HEADLINE_L']['text'], texts['HEADLINE'])
        self.assertEqual(large['HEADLINE_S']['text'], canva.BLANK)
        self.assertEqual(small['HEADLINE_S']['text'], texts['HEADLINE'])
        self.assertEqual(small['HEADLINE_L']['text'], canva.BLANK)

    def test_current_week_official_edict_is_restored_once_but_older_rejection_is_kept(self):
        source_id = db.exec_("""INSERT INTO sources(name,url,kind,priority,official,local_scope)
                               VALUES(?,?,?,?,?,?)""",
                            ('Prueba tablón oficial', 'https://www.sedeelectronica.lalinea.es/edictos/',
                             'edictos', 99, 1, 1))
        source = db.row('SELECT * FROM sources WHERE id=?', (source_id,))
        published = datetime.now(timezone.utc).isoformat()
        item = {'title': 'Edicto: Bases de una convocatoria municipal',
                'url': 'https://www.sedeelectronica.lalinea.es/edictos/?codigo=restore-test',
                'excerpt': 'Bases de una convocatoria municipal', 'published_at': published}
        cid = sources.add_candidate(item['title'], item['url'], item['excerpt'], source['name'],
                                    source_id, published,
                                    {'kind': 'edictos', 'priority': 99, 'official': 1, 'local_scope': 1})
        db.exec_("UPDATE candidates SET status='archived',editorial_priority='this_week' WHERE id=?", (cid,))
        self.assertIsNotNone(sources._archived_official_candidate(item, source))
        self.assertEqual(db.row('SELECT status FROM candidates WHERE id=?', (cid,))['status'], 'archived')
        db.exec_("INSERT INTO articles(candidate_id,headline,status) VALUES(?,?,?)",
                 (cid, 'Borrador anterior del edicto', 'rejected'))
        self.assertEqual(sources._resurface_archived_official(item, source), cid)
        self.assertEqual(db.row('SELECT status FROM candidates WHERE id=?', (cid,))['status'], 'new')
        self.assertEqual(db.row('SELECT editorial_priority FROM candidates WHERE id=?', (cid,))['editorial_priority'], 'this_week')

        requested_again = dict(item, url=item['url'] + '-requested-again')
        requested_id = sources.add_candidate(requested_again['title'], requested_again['url'], requested_again['excerpt'], source['name'],
                                             source_id, published,
                                             {'kind': 'edictos', 'priority': 99, 'official': 1, 'local_scope': 1})
        db.exec_("UPDATE candidates SET status='archived',editorial_priority='no_interest' WHERE id=?", (requested_id,))
        self.assertEqual(sources._archive_recovery_status(requested_again, source)[1], 'recuperable')
        self.assertEqual(sources._resurface_archived_official(requested_again, source), requested_id)
        self.assertEqual(db.row('SELECT status,editorial_priority FROM candidates WHERE id=?', (requested_id,)),
                         {'status': 'new', 'editorial_priority': 'undecided'})

        older_date = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        older = dict(item, url=item['url'] + '-older', published_at=older_date)
        older_id = sources.add_candidate(older['title'], older['url'], older['excerpt'], source['name'],
                                          source_id, older_date,
                                          {'kind': 'edictos', 'priority': 99, 'official': 1, 'local_scope': 1})
        db.exec_("UPDATE candidates SET status='archived',editorial_priority='no_interest' WHERE id=?", (older_id,))
        self.assertEqual(sources._archive_recovery_status(older, source)[1], 'descartado_explicito')
        self.assertIsNone(sources._resurface_archived_official(older, source))
        self.assertEqual(db.row('SELECT status FROM candidates WHERE id=?', (older_id,))['status'], 'archived')

    def test_placsp_probe_does_not_consume_contract_changes(self):
        updated = datetime.now(timezone.utc).isoformat()
        uid = 'probe-state-must-remain-' + str(int(datetime.now(timezone.utc).timestamp()))
        feed = f'''<feed><entry><id>{uid}</id><title>Servicio municipal de prueba</title>
          <link href="https://contrataciondelestado.es/anuncio/{uid}" />
          <updated>{updated}</updated><summary>Órgano de Contratación: Ayuntamiento de La Línea de la Concepción; Estado: PUB</summary>
        </entry></feed>'''
        with patch('app.sources.requests.get', return_value=Response(feed)):
            items = sources.parse_placsp({'url': sources.PLACSP_FEED}, remember_state=False)
        self.assertEqual(len(items), 1)
        self.assertIsNone(db.row('SELECT id FROM placsp_state WHERE id=?', (uid,)))

    def test_photo_sent_to_canva_keeps_full_frame_for_editing(self):
        from PIL import ImageDraw
        path = Path(_temp.name) / 'crop-source.jpg'
        image = Image.new('RGB', (1600, 1000), 'black')
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 799, 999), fill='red')
        draw.rectangle((800, 0, 1599, 999), fill='blue')
        image.save(path)
        output = canva._photo_for_canvas(path, {})
        prepared = Image.open(io.BytesIO(output)).convert('RGB')
        self.assertEqual(prepared.size, (1600, 1000))
        left_pixel, right_pixel = prepared.getpixel((400, 500)), prepared.getpixel((1200, 500))
        self.assertGreater(left_pixel[0], 240)
        self.assertGreater(right_pixel[2], 240)

    def test_search_news_queries_google_news_and_adds_recent_results(self):
        today = format_datetime(datetime.now(timezone.utc))
        item = {'title': 'La Línea amplía los horarios del puerto', 'url': 'https://example.org/puerto',
                'excerpt': 'Nuevos horarios en el puerto', 'published_at': today, 'outlet': 'Diario local'}
        with patch.object(sources, 'parse_rss', return_value=[item]) as rss, \
             patch.object(sources, 'add_candidate', return_value=41) as add:
            result = sources.search_news('horarios puerto')
        self.assertEqual(result['candidate_ids'], [41])
        self.assertEqual(result['added'], 1)
        self.assertIn('horarios%20puerto', rss.call_args.args[0]['url'])
        self.assertEqual(add.call_args.args[3], 'Búsqueda por tema')

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

    def test_placsp_keeps_a_tender_open_when_its_feed_update_is_old(self):
        now = datetime.now(timezone.utc)
        updated = (now - timedelta(days=31)).isoformat()
        deadline = (now + timedelta(days=2)).isoformat(timespec='seconds')
        xml = f'''<feed><entry><title>Mejora de instalaciones en La Línea</title>
          <id>urn:test:open-tender-retention</id><link rel="alternate" href="https://contrataciondelestado.es/detalle/open-test" />
          <updated>{updated}</updated><summary>Órgano de Contratación: Alcaldía del Ayuntamiento de la Línea de la Concepción; Estado: PUB</summary>
          <cac:TenderSubmissionDeadlinePeriod><cbc:EndDate>{deadline}</cbc:EndDate></cac:TenderSubmissionDeadlinePeriod>
          </entry></feed>'''
        with patch.object(sources.requests, 'get', return_value=Response(xml)), \
             patch.object(sources, 'similar_to_published', return_value=False), \
             patch.object(sources, 'find_tender', return_value=None):
            results = sources.parse_placsp({'url': sources.PLACSP_FEED})
            self.assertEqual(len(results), 1)
            self.assertIn('Plazo hasta ' + deadline, results[0]['excerpt'])
            cid = sources.add_candidate(results[0]['title'], results[0]['url'], results[0]['excerpt'],
                                        'PLACSP', published_at=results[0]['published_at'],
                                        source_meta={'kind': 'procurement', 'priority': 100, 'official': 1, 'local_scope': 1})
        self.assertIsNotNone(cid)
        sources.archive_stale()
        self.assertEqual(db.row('SELECT status FROM candidates WHERE id=?', (cid,))['status'], 'new')

    def test_placsp_reports_a_changed_minor_contract(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        def feed(updated):
            return f'''<feed><entry><title>Servicio municipal de apoyo en La Línea</title>
              <id>urn:test:minor-contract-change</id><link rel="alternate" href="https://contrataciondelestado.es/detalle/minor-test" />
              <updated>{updated.isoformat()}</updated><summary>Órgano de Contratación: Ayuntamiento de La Línea de la Concepción</summary>
              </entry></feed>'''
        with patch.object(sources.requests, 'get', side_effect=[Response(feed(now)), Response(feed(now + timedelta(minutes=1))) ]):
            first = sources.parse_placsp({'url': sources.PLACSP_MENORES})
            second = sources.parse_placsp({'url': sources.PLACSP_MENORES})
        self.assertEqual(first[0]['title'], 'Contrato menor: Servicio municipal de apoyo en La Línea')
        self.assertTrue(second[0]['title'].startswith('Actualización de contrato menor:'))

    def test_edictos_fall_back_to_search_if_the_board_markup_changes(self):
        found = [{'title': 'Edicto: plazo de exposición pública', 'url': 'https://www.sedeelectronica.lalinea.es/edictos/edicto?codigo=abc',
                  'excerpt': 'Publicación oficial', 'published_at': datetime.now(timezone.utc).isoformat()}]
        with patch.object(sources, 'fetch_edictos_html', return_value='<html><body>formato actualizado</body></html>'), \
             patch.object(sources, 'edictos_from_search', return_value=found):
            result = sources.parse_edictos({'url': 'https://www.sedeelectronica.lalinea.es/edictos/edicto/buscar-edictos-filtro-pub?primeraBusqueda=true'})
        self.assertEqual(result, found)

    def test_edictos_keep_recent_table_rows_without_matching_detail_links(self):
        today = datetime.now(timezone.utc).strftime('%d/%m/%Y')
        old = (datetime.now(timezone.utc) - timedelta(days=30)).strftime('%d/%m/%Y')
        page = f'''<table>
          <tr><td>{today}</td><td>31/01/2027</td><td>BASES PUEBLO NAVIDEÑO NAVIDAD Y REYES 2026/27</td></tr>
          <tr><td>{today}</td><td>31/10/2026</td><td><a href="/anuncio/28006">Consulta pública sobre una ordenanza</a></td></tr>
          <tr><td>{old}</td><td>31/12/2026</td><td>Una publicación antigua</td></tr>
        </table>'''
        source = {'url': 'https://www.sedeelectronica.lalinea.es/edictos/edicto/buscar-edictos-filtro-pub?primeraBusqueda=true'}
        with patch.object(sources, 'fetch_edictos_html', return_value=page), \
             patch.object(sources, 'edictos_from_search', return_value=[]):
            result = sources.parse_edictos(source)
        self.assertEqual(len(result), 2)
        self.assertIn('BASES PUEBLO NAVIDEÑO', result[0]['title'])
        self.assertIn('/edictos/publico?idOrgan=23#', result[0]['url'])
        self.assertTrue(result[1]['url'].endswith('/anuncio/28006'))

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
             patch.object(pipeline.photos, 'search_real_photos', return_value=[]):
            with self.assertRaises(ValueError): pipeline.draft_candidate(cid)
            research = pipeline.investigate_candidate(cid)
            self.assertEqual(db.row('SELECT status FROM candidates WHERE id=?', (cid,))['status'], 'researched')
            self.assertTrue(research['caveats'])  # Free mode identifies lack of independent verification.
            aid = pipeline.draft_candidate(cid)
            article = db.row('SELECT * FROM articles WHERE id=?', (aid,))
            self.assertEqual(article['status'], 'draft')
            self.assertEqual(article['workflow'], 'source_draft')
            self.assertIsNone(article['render_path'])  # Canva has not exported a design yet.
            self.assertIsNone(article['template_id'])  # No PPTX fallback.
            self.assertIn('según publica Fuente municipal', article['body'])
            self.assertLessEqual(len(article['body']), 2200)
            self.assertEqual(pipeline.submit_candidate(cid), aid)
            self.assertEqual(db.row('SELECT status FROM articles WHERE id=?', (aid,))['status'], 'review_ready')

    def test_free_draft_preserves_main_fact_when_excerpt_only_gives_context(self):
        draft = ai.free_draft({'title': 'La Línea inaugura una nueva plaza',
                               'excerpt': 'Los vecinos asistieron al acto de apertura.',
                               'source_name': 'Prensa local'})
        self.assertTrue(draft['body'].startswith('La Línea inaugura una nueva plaza'))
        self.assertIn('Los vecinos asistieron', draft['body'])
        self.assertEqual(draft['social_text'], draft['body'])
        self.assertLessEqual(len(draft['body']), 2200)

    def test_only_canva_export_produces_a_visible_image(self):
        cid = sources.add_candidate('Un nuevo espacio cultural abre en La Atunara',
                                    'https://example.org/cultura', 'La programación incluye conciertos',
                                    'Fuente local', source_meta={'priority': 75, 'official': 1})
        with patch.object(sources, 'fetch_article_text', return_value=''), \
             patch.object(pipeline.photos, 'search_real_photos', return_value=[]):
            pipeline.investigate_candidate(cid)
            aid = pipeline.draft_candidate(cid)
        self.assertIsNone(main.kit(aid)['image_url'])
        with self.assertRaises(HTTPException) as missing:
            main.render_file(aid)
        self.assertEqual(missing.exception.status_code, 404)

        photo = Path(_temp.name) / 'own.jpg'
        Image.new('RGB', (80, 100), '#1f5eff').save(photo)
        selected = {'url': 'upload:own', 'license': 'Foto propia', 'source': 'Redacción', 'publish_safe': True}
        db.exec_('UPDATE articles SET image_local=?,image_url=?,image_source=?,image_license=?,image_candidates_json=? WHERE id=?',
                 (str(photo), selected['url'], selected['source'], selected['license'], json.dumps([selected]), aid))
        article = db.row('SELECT * FROM articles WHERE id=?', (aid,))
        png = io.BytesIO()
        Image.new('RGB', (1080, 1350), '#1f5eff').save(png, format='PNG')
        responses = []
        def mock_api(method, path, **kwargs):
            responses.append((method, path, kwargs))
            if path.endswith('/dataset'):
                return {'dataset': {k: {'type': v} for k, v in {'HEADLINE': 'text', 'SUMMARY': 'text', 'SECTION': 'text', 'PHOTO': 'image'}.items()}}
            # Respuestas con la forma documentada por la API Connect de Canva.
            if method == 'GET' and '/asset-uploads/' in path:
                return {'job': {'id': 'up', 'status': 'success', 'asset': {'id': 'own-asset'}}}
            if method == 'GET' and '/autofills/' in path:
                return {'job': {'id': 'af', 'status': 'success', 'result': {'type': 'create_design', 'design': {
                    'id': 'canva-design', 'url': 'https://www.canva.com/design/canva-design/edit',
                    'urls': {'edit_url': 'https://www.canva.com/api/temp-edit', 'view_url': 'https://www.canva.com/api/temp-view'}}}}}
            if method == 'GET' and '/exports/' in path:
                return {'job': {'id': 'ex', 'status': 'success', 'urls': ['https://export.canva.com/test.png']}}
            return {'job': {'id': 'test-job', 'status': 'in_progress'}}
        class Download:
            content = png.getvalue()
            def raise_for_status(self): pass
        with patch.object(canva, 'CLIENT_ID', 'test'), patch.object(canva, 'CLIENT_SECRET', 'test'), \
             patch.object(canva, 'TEMPLATE_ID', 'EAHWTjWEEnA'), patch.object(canva, 'PUBLIC_BASE_URL', 'https://example.org'), \
             patch.object(canva, 'connected', return_value=True), patch.object(canva, '_api', side_effect=mock_api), \
             patch.object(canva.time, 'sleep'), patch.object(canva.requests, 'get', return_value=Download()):
            result = canva.create_design(article)
            self.assertTrue(result['exported'])
            self.assertEqual(result['url'], 'https://www.canva.com/design/canva-design/edit')
            self.assertEqual(result['design_id'], 'canva-design')
            self.assertEqual(db.row('SELECT exported FROM canva_designs WHERE article_id=?', (aid,))['exported'], 1)
            fields = next(kwargs['json']['data'] for method, path, kwargs in responses if path == '/autofills')
            self.assertEqual(fields['HEADLINE']['text'], article['headline'])
            self.assertEqual(fields['PHOTO']['asset_id'], 'own-asset')
            self.assertTrue(main.kit(aid)['image_url'])
            self.assertEqual(main.render_file(aid).media_type, 'image/png')

            # Editing invalidates the export even while the old PNG remains on disk.
            main.edit_article(aid, main.EditArticle(headline='Otro titular'))
            self.assertIsNone(main.kit(aid)['image_url'])
            with self.assertRaises(HTTPException): main.render_file(aid)
            with self.assertRaisesRegex(ValueError, 'vuelve a crear'):
                canva.export_design(db.row('SELECT * FROM articles WHERE id=?', (aid,)))

    def test_legacy_pptx_preview_is_invalidated_during_upgrade(self):
        aid = db.exec_("INSERT INTO articles(headline,status,render_path) VALUES('Archivo anterior','approved','legacy.png')")
        db.exec_('INSERT INTO canva_designs(article_id,design_id,url,exported) VALUES(?,?,?,1)',
                 (aid, 'old-design', 'https://www.canva.com/design/old'))
        png = Path(os.environ['RENDER_DIR']) / f'article_{aid}.png'
        Image.new('RGB', (80, 100), '#1f5eff').save(png)
        db.init_db()
        self.assertEqual(db.row('SELECT exported FROM canva_designs WHERE article_id=?', (aid,))['exported'], 0)
        upgraded = db.row('SELECT status,render_path FROM articles WHERE id=?', (aid,))
        self.assertEqual(upgraded['status'], 'review_ready')
        self.assertIsNone(upgraded['render_path'])
        self.assertIsNone(main.kit(aid)['image_url'])
        with self.assertRaises(HTTPException):
            main.render_file(aid)

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
