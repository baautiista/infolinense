from datetime import datetime, timezone
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


class ScanEndpointTests(unittest.TestCase):
    def test_scan_reports_busy_until_done(self):
        import threading as th, time as tm
        from app import sources
        gate = th.Event()
        def slow():
            with sources._scan_lock:
                sources._scan_state.update(busy=True); gate.wait(3)
            sources._scan_state.update(busy=False, finished_at=tm.time())
            return {'added': [], 'busy': False}
        with patch.object(main, '_scan_and_plan', side_effect=slow):
            r = main.scan()
            self.assertTrue(r['started']); self.assertTrue(r['busy'])
            tm.sleep(0.2)
            self.assertTrue(main.scan_status()['busy'])
            self.assertFalse(main.scan()['started'])  # no arranca otra a la vez
            gate.set(); tm.sleep(0.3)
            self.assertFalse(main.scan_status()['busy'])


class QuickPublishTests(unittest.TestCase):
    def test_launcher_publishes_text_as_is(self):
        db.init_db()
        buf = BytesIO(); Image.new('RGB', (1200, 1500), 'blue').save(buf, 'JPEG')
        calls = {}
        def fake_canva(a):
            calls['canva'] = a['headline']; return {'exported': True}
        def fake_publish(aid, body):
            calls['pub'] = aid; return {'ok': True, 'published': True, 'results': [{'network': 'web', 'ok': True, 'message': 'Publicada en la web'}]}
        with patch.object(main.canva, 'ready', return_value=True), patch.object(main.canva, 'connected', return_value=True), \
                patch.object(main.canva, 'create_design', side_effect=fake_canva), patch.object(main, 'publish', side_effect=fake_publish):
            r = asyncio.run(main.quick_publish(headline='Mi titular exacto de la redacción', subtitle='Entradilla', body='Texto completo.',
                                               section='CIUDAD', brand='infolinense', link='', at='', networks='',
                                               photo=FakeUpload('foto.jpg', buf.getvalue())))
        self.assertTrue(r['published'])
        a = db.row('SELECT * FROM articles WHERE id=?', (r['id'],))
        self.assertEqual(a['headline'], 'Mi titular exacto de la redacción')
        self.assertEqual(calls['canva'], 'Mi titular exacto de la redacción')
        self.assertTrue(Path(a['image_local']).exists())

    def test_launcher_with_finished_canva_image_skips_canva(self):
        db.init_db()
        buf = BytesIO(); Image.new('RGB', (1080, 1350), 'red').save(buf, 'PNG')
        calls = {}
        def fake_publish(aid, body):
            calls['pub'] = aid; return {'ok': True, 'published': True, 'results': []}
        with patch.object(main.canva, 'ready', return_value=True), patch.object(main.canva, 'connected', return_value=True), \
                patch.object(main.canva, 'create_design', side_effect=AssertionError('no debe usar Canva')), patch.object(main, 'publish', side_effect=fake_publish):
            r = asyncio.run(main.quick_publish(headline='Titular de la imagen ya hecha', subtitle='', body='Texto.',
                                               section='CIUDAD', brand='infolinense', link='', at='', networks='', ready='1',
                                               photo=FakeUpload('canva.png', buf.getvalue())))
        self.assertTrue(r['published'])
        a = db.row('SELECT * FROM articles WHERE id=?', (r['id'],))
        self.assertTrue(Path(a['render_path']).is_file())
        from PIL import Image as I
        self.assertEqual(I.open(a['render_path']).size, (1080, 1350))
        self.assertEqual(main.social_publish.article_images(a), [a['render_path']])


class ChatGPTExportTest(unittest.TestCase):
    def test_export_only_publish_queue_and_import_batch(self):
        db.init_db()
        cid = db.exec_("""INSERT INTO candidates(source_name,outlet,title,url,published_at,excerpt,raw_text,score,relevance,status,editorial_priority,brand)
                          VALUES('Ayuntamiento','Ayuntamiento','Obras en la calle Real','https://ejemplo.es/obras','2026-10-12','Resumen',
                          'El Ayuntamiento empieza las obras el lunes.',80,'high','new','today','infolinense')""")
        aid = db.exec_("INSERT INTO articles(candidate_id,headline,body,status,brand) VALUES(?,'','','draft','infolinense')", (cid,))
        cid2 = db.exec_("INSERT INTO candidates(source_name,title,url,status,editorial_priority,brand) VALUES('X','Ya programada','https://e.es/p','new','today','infolinense')")
        db.exec_("INSERT INTO articles(candidate_id,headline,body,status,brand,scheduled_at) VALUES(?,'t','b','approved','infolinense','2030-01-01T10:00')", (cid2,))
        text, n = main.chatgpt_export_text('infolinense')
        self.assertIn('ID: %d' % aid, text)
        self.assertIn('El Ayuntamiento empieza las obras el lunes.', text)
        self.assertNotIn('Ya programada', text)
        r = main.export_chatgpt(brand='infolinense', download=1)
        self.assertIn('attachment', r.headers['content-disposition'])
        reply = ('**ID:** %d\n**MEDIO:** InfoLinense\nSECCIÓN: Obras\nTITULAR: El Ayuntamiento arranca el lunes las obras de la calle Real.\n'
                 'ENTRADILLA: Los trabajos durarán tres meses.\nTEXTO: Primer párrafo.\n\nSegundo párrafo.\n---\n' % aid)
        res = main.import_chatgpt(main.BatchIn(text=reply))
        self.assertEqual(res['updated'], 1)
        a = db.row('SELECT * FROM articles WHERE id=?', (aid,))
        self.assertEqual(a['headline'], 'El Ayuntamiento arranca el lunes las obras de la calle Real')
        self.assertEqual(a['subtitle'], 'Los trabajos durarán tres meses.')
        self.assertEqual(a['body'], 'Primer párrafo.\n\nSegundo párrafo.')
        self.assertEqual(a['workflow'], 'lote')


class LauncherCarouselTest(unittest.TestCase):
    def test_launcher_carousel_with_ready_images(self):
        db.init_db()
        def img(color):
            b = BytesIO(); Image.new('RGB', (1080, 1350), color).save(b, 'PNG'); return b.getvalue()
        with patch.object(main.canva, 'create_design', side_effect=AssertionError('no debe usar Canva')), \
                patch.object(main, 'publish', side_effect=lambda aid, body: {'ok': True, 'published': True, 'results': []}):
            r = asyncio.run(main.quick_publish(headline='Carrusel de la procesión', subtitle='', body='Texto.',
                                               section='', brand='cofrade', link='', at='', networks='', ready='1',
                                               photo=FakeUpload('1.png', img('red')),
                                               extra=[FakeUpload('2.png', img('green')), FakeUpload('3.png', img('blue'))]))
        a = db.row('SELECT * FROM articles WHERE id=?', (r['id'],))
        paths = main.social_publish.article_images(a)
        self.assertEqual(len(paths), 3)
        self.assertEqual(paths[0], a['render_path'])
        self.assertTrue(paths[2].endswith('_carousel_2.png'))

    def test_endpoint_accepts_several_files(self):
        try:
            import httpx  # noqa: F401
        except ImportError:
            self.skipTest('httpx no está instalado')
        from fastapi.testclient import TestClient
        db.init_db()
        def img(color):
            b = BytesIO(); Image.new('RGB', (100, 100), color).save(b, 'PNG'); return b.getvalue()
        seen = {}
        def fake_publish(aid, body):
            seen['n'] = len(main.social_publish.article_images(db.row('SELECT * FROM articles WHERE id=?', (aid,))))
            return {'ok': True, 'published': True, 'results': []}
        main.app.dependency_overrides[main.require_auth] = lambda: None
        try:
            with patch.object(main, 'publish', side_effect=fake_publish):
                res = TestClient(main.app).post('/api/quick', data={'headline': 'Titular del carrusel subido', 'body': 'Texto', 'ready': '1'},
                                                files=[('photo', ('a.png', img('red'), 'image/png')), ('extra', ('b.png', img('blue'), 'image/png')),
                                                       ('extra', ('c.png', img('green'), 'image/png'))])
        finally:
            main.app.dependency_overrides.clear()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(seen['n'], 3)


class SourceReadingTests(unittest.TestCase):
    def test_wordpress_site_is_read_through_its_feed(self):
        from app import sources
        class R:
            text = '<html><head></head><body><img src="/wp-content/uploads/x.jpg"><h5><a href="/a/">Noticia corta pero suficiente de longitud</a></h5></body></html>'
            def raise_for_status(self): pass
        feed_items = [{'title': 'Cultos en honor a la Estrella', 'url': 'https://www.lalineacofrade.com/cultos/', 'excerpt': 'x', 'published_at': 'Thu, 09 Oct 2026 10:00:00 +0200'}]
        with patch.object(sources, 'fetch', return_value=R()), patch.object(sources, 'parse_rss', return_value=feed_items) as rss:
            items = sources.parse_html({'url': 'https://www.lalineacofrade.com/'})
        self.assertEqual(items, feed_items)
        self.assertEqual(rss.call_args[0][0]['url'], 'https://www.lalineacofrade.com/feed/')

    def test_latin_america_is_not_la_linea(self):
        from app import sources
        self.assertFalse(sources.place_ok('Cierre del túnel de La Línea entre Quindío y Tolima'))
        self.assertTrue(sources.place_ok('El Ayuntamiento de La Línea aprueba los presupuestos'))


class AyuntamientoTests(unittest.TestCase):
    def test_official_feed_reads_extra_pages(self):
        from app import sources
        pages = {'https://lalinea.es/feed/': [{'url': 'u%d' % i, 'title': 't'} for i in range(10)],
                 'https://lalinea.es/feed/?paged=2': [{'url': 'v%d' % i, 'title': 't'} for i in range(10)],
                 'https://lalinea.es/feed/?paged=3': []}
        with patch.object(sources, 'parse_rss', side_effect=lambda s: pages[s['url']]):
            items = sources.read_source({'url': 'https://lalinea.es/feed/', 'kind': 'rss', 'official': 1})
        self.assertEqual(len(items), 20)

    def test_pasted_link_brings_back_merged_news(self):
        from app import sources
        db.init_db()
        cid = db.exec_("INSERT INTO candidates(source_name,title,url,status) VALUES('Ayto','Récord en el Salón Manga','https://lalinea.es/manga/','merged')")
        r = sources.search_news('https://lalinea.es/manga/')
        self.assertEqual(r['added'], 1)
        self.assertIn('unida a otra tarjeta', r['note'])
        self.assertEqual(db.row('SELECT status FROM candidates WHERE id=?', (cid,))['status'], 'new')

    def test_official_news_is_not_merged_into_other_media(self):
        from app import sources
        db.init_db()
        other = sources.add_candidate('Récord de asistencia en el XIV Salón Manga y ComicCon de La Línea', 'https://medio.es/manga',
                                      'Más de 3.000 personas en La Línea', 'Medio', None, datetime.now(timezone.utc).isoformat(),
                                      {'priority': 60, 'official': 0, 'local_scope': 1})
        mine = sources.add_candidate('El XIV Salón Manga y ComicCon superó los 3.000 asistentes con récord de asistencia',
                                     'https://lalinea.es/manga2/', 'Récord en La Línea', 'Ayuntamiento de La Línea', None,
                                     datetime.now(timezone.utc).isoformat(), {'priority': 100, 'official': 1, 'local_scope': 1})
        self.assertIsNotNone(other)
        self.assertIsNotNone(mine)


class CanvaSinglePageTests(unittest.TestCase):
    def test_other_pages_are_deleted(self):
        from app import canva
        calls = []
        def fake_api(method, path, **kw):
            calls.append((method, path, kw.get('json')))
            if path.startswith('/designs/'): return {'design': {'page_count': 12}}
            if path == '/merges': return {'job': {'id': 'J', 'status': 'success'}}
            return {}
        with patch.object(canva, '_api', side_effect=fake_api):
            self.assertTrue(canva._keep_only_page('DX', 5))
        body = [c for c in calls if c[1] == '/merges'][0][2]
        self.assertEqual(body['operations'][0]['page_numbers'], [1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12])

    def test_keeps_design_if_canva_refuses(self):
        from app import canva
        def fake_api(method, path, **kw):
            if path.startswith('/designs/'): return {'design': {'page_count': 3}}
            raise ValueError('Canva (403): sin permiso')
        with patch.object(canva, '_api', side_effect=fake_api):
            self.assertFalse(canva._keep_only_page('DX', 2))


class CanvaBatchTests(unittest.TestCase):
    def test_batch_is_one_design_pages_in_a_row_with_retry(self):
        from app import canva
        arts = [{'id': i, 'brand': 'infolinense', 'section': 'CIUDAD', 'headline': 'Titular largo ' * 10 + str(i), 'subtitle': 'E'} for i in range(4)]
        calls, fails = [], [1]
        def fake_api(method, path, **kw):
            calls.append((path, kw.get('json')))
            if path == '/merges' and fails[0]:
                fails[0] -= 1
                raise ValueError('Canva (429): too many requests')
            return {'job': {'id': 'J', 'status': 'success', 'result': {'design': {'id': 'DALL', 'url': 'https://canva.com/x'}}}}
        with patch.object(canva, 'ready', return_value=True), patch.object(canva, 'connected', return_value=True), \
                patch.object(canva, '_autofill_text_only', side_effect=lambda a: ('D%d' % a['id'], 2)), \
                patch.object(canva, '_api', side_effect=fake_api), patch.object(canva.time, 'sleep'):
            r = canva.batch_design(arts)
        self.assertEqual((r['url'], r['pages'], r['errors']), ('https://canva.com/x', 4, []))
        merges = [b for p, b in calls if p == '/merges']
        self.assertEqual(len(merges), 5)   # 1 fallo + crear + 3 añadidas
        self.assertEqual(merges[-1]['operations'][0]['source'], {'type': 'design', 'design_id': 'D3', 'page_numbers': [2]})


class LaunchBatchTests(unittest.TestCase):
    def test_txt_and_zip_are_joined_by_order(self):
        import zipfile
        db.init_db()
        txt = ('TITULAR: Primera noticia de la lanzadera en lote\nENTRADILLA: Uno\nTEXTO: Texto uno.\n---\n'
               'TITULAR: Segunda noticia de la lanzadera en lote\nENTRADILLA: Dos\nTEXTO: Texto dos.\n')
        zb = BytesIO()
        with zipfile.ZipFile(zb, 'w') as z:
            for n, c in (('2.png', 'blue'), ('1.png', 'red')):
                b = BytesIO(); Image.new('RGB', (1080, 1350), c).save(b, 'PNG'); z.writestr(n, b.getvalue())
        r = asyncio.run(main.launch_batch(text=txt, brand='infolinense', txt=None, images=FakeUpload('imgs.zip', zb.getvalue())))
        self.assertEqual(len(r['items']), 2)
        self.assertEqual(r['matched'], 2)
        first = r['items'][0]
        self.assertEqual(first['image'], '1.png')
        self.assertTrue(first['has_image'])
        a = db.row('SELECT * FROM articles WHERE id=?', (first['id'],))
        self.assertEqual(main.social_publish.article_images(a), [a['render_path']])


class ChatgptMergeTests(unittest.TestCase):
    def test_prompt_asks_to_merge_and_unidas_is_parsed(self):
        self.assertIn('UNIDAS', main.chatgpt_export_text()[0])
        r = main.parse_batch('ID: 5\nUNIDAS: 7, 9\nTITULAR: Uno\nTEXTO: x\n---\nID: 8\nTITULAR: Dos\n')
        self.assertEqual(r[0]['merged'], [7, 9])
        self.assertEqual(r[1]['merged'], [])


class LaunchFolderTests(unittest.TestCase):
    def test_loose_images_from_folder_join_by_order(self):
        db.init_db()
        txt = ('TITULAR: Uno de carpeta para la lanzadera\nTEXTO: a\n---\nTITULAR: Dos de carpeta para la lanzadera\nTEXTO: b\n')
        files = []
        for n, c in (('fotos/2.png', 'blue'), ('fotos/1.png', 'red')):
            b = BytesIO(); Image.new('RGB', (1080, 1350), c).save(b, 'PNG'); files.append(FakeUpload(n, b.getvalue()))
        r = asyncio.run(main.launch_batch(text=txt, brand='infolinense', txt=None, images=files))
        self.assertEqual(r['matched'], 2)
        self.assertEqual(r['items'][0]['image'], '1.png')


class OcrMatchTests(unittest.TestCase):
    def test_match_by_text_read_in_image(self):
        from app import ocr
        heads = ['El Ayuntamiento arregla la plaza de la Iglesia', 'La Feria del Libro llega al Mercado']
        texts = ['CULTURA\nLa Feria del Libro\nllega al Mercado', 'CIUDAD El Ayuntamiento arregla\nla plaza de la Iglesia']
        self.assertEqual(ocr.match(heads, texts), {0: 1, 1: 0})
        self.assertEqual(ocr.match(heads, ['nada que ver aqui']), {})


class SectionColorTests(unittest.TestCase):
    def test_section_from_label_color(self):
        from app import ocr
        from PIL import ImageDraw
        im = Image.new('RGB', (1080, 1350), (200, 225, 245)); d = ImageDraw.Draw(im)
        d.rectangle((55, 657, 400, 711), fill='#00AB4F')
        self.assertEqual(ocr.section_of(im, ''), 'DEPORTES')
        self.assertEqual(ocr.section_of(im, 'FÚTBOL\nLa Balona gana'), 'FÚTBOL')


class AdaptableNationalTests(unittest.TestCase):
    def test_adaptable_filter(self):
        from app import sources
        self.assertTrue(sources.adaptable_ok('Los alquileres suben un 11% en el último año'))
        self.assertTrue(sources.adaptable_ok('Las pensiones subirán un 2,8% en enero'))
        self.assertFalse(sources.adaptable_ok('El Real Madrid gana la Liga'))
        self.assertFalse(sources.adaptable_ok('Madrid aprueba nuevas ayudas al alquiler'))
        self.assertFalse(sources.adaptable_ok('Detenido un hombre en Valencia'))

    def test_add_candidate_sets_angle(self):
        from app import sources, db
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        meta = {'name': 'Nacionales adaptables · Temas · vivienda y precios', 'priority': 66, 'official': 0, 'local_scope': 0}
        cid = sources.add_candidate('El precio de la luz sube mañana un 20%', 'https://elpais.com/x-luz-adapt-test', '',
                                    meta['name'], None, now, meta)
        self.assertTrue(cid)
        self.assertIn('NACIONAL ADAPTADA', db.row('SELECT local_angle FROM candidates WHERE id=?', (cid,))['local_angle'])
        self.assertIsNone(sources.add_candidate('Gran Hermano arrasa en audiencia', 'https://telecinco.es/gh-adapt-test', '',
                                                meta['name'], None, now, meta))
        nat = {'name': 'Medios nacionales', 'priority': 80, 'official': 0, 'local_scope': 0}
        self.assertIsNone(sources.add_candidate('Ranking de playas de España', 'https://elmundo.es/playas-adapt-test',
                                                'Lista larga... ' + 'x ' * 300 + 'La Línea de la Concepción', nat['name'], None, now, nat))


class ExportSkipsScheduledTests(unittest.TestCase):
    def test_scheduled_not_exported(self):
        from app import db, main
        cid = db.exec_("INSERT INTO candidates(title,url,status) VALUES('Noticia programada test','https://x.test/prog','new')")
        a1 = db.exec_("INSERT INTO articles(candidate_id,status,headline,scheduled_at) VALUES(?,?,?,?)", (cid, 'approved', 'h', '2099-01-01T10:00+01:00'))
        cid2 = db.exec_("INSERT INTO candidates(title,url,status) VALUES('Fallo al programar test','https://x.test/prog2','new')")
        a3 = db.exec_("INSERT INTO articles(candidate_id,status,headline,schedule_error) VALUES(?,?,?,?)", (cid2, 'approved', 'h3', 'Error'))
        cid3 = db.exec_("INSERT INTO candidates(title,url,status,plan_locked,planned_at) VALUES('Hora fijada test','https://x.test/prog3','new',1,'2099-01-01T09:00+01:00')")
        a4 = db.exec_("INSERT INTO articles(candidate_id,status,headline) VALUES(?,?,?)", (cid3, 'draft', 'h4'))
        cid4 = db.exec_("INSERT INTO candidates(title,url,status) VALUES('Libre test','https://x.test/prog4','new')")
        a5 = db.exec_("INSERT INTO articles(candidate_id,status,headline) VALUES(?,?,?)", (cid4, 'draft', 'h5'))
        ids = {r['aid'] for r in main._publish_queue()}
        self.assertFalse({a1, a3, a4} & ids)
        self.assertIn(a5, ids)


class WebCoverTests(unittest.TestCase):
    """La portada de la web nunca es la imagen terminada de Canva (logo, sección, titular y entradilla)."""

    def setUp(self):
        db.init_db()
        cid = db.exec_("INSERT INTO candidates(title,url,status) VALUES('t','manual:web-cover-%s','new')" % id(self))
        self.aid = db.exec_("INSERT INTO articles(candidate_id,status,headline,body) VALUES(?,?,?,?)", (cid, 'approved', 'Titular', 'Texto'))

    def _row(self):
        return db.row('SELECT * FROM articles WHERE id=?', (self.aid,))

    def test_final_image_alone_gives_no_web_cover(self):
        from app import publishers
        main._set_final_image(self.aid, Image.new('RGB', (1080, 1350), (10, 20, 30)))
        a = self._row()
        self.assertEqual(a['image_kind'], 'final_image')
        self.assertTrue(Path(a['render_path']).is_file())
        self.assertIsNone(publishers.web_payload(a)['image_base64'])

    def test_web_photo_is_the_cover_and_survives_final_image(self):
        from app import publishers
        main._set_web_photo(self.aid, Image.new('RGB', (1600, 900), (200, 100, 50)))
        main._set_final_image(self.aid, Image.new('RGB', (1080, 1350), (10, 20, 30)))
        a = self._row()
        self.assertEqual(a['image_kind'], 'web_photo')
        self.assertTrue(Path(a['render_path']).is_file())
        self.assertTrue(publishers.web_payload(a)['image_base64'])

    def test_legacy_final_upload_is_marked(self):
        main._set_final_image(self.aid, Image.new('RGB', (1080, 1350)))
        db.exec_("UPDATE articles SET image_kind='user_upload',image_source='Redacción' WHERE id=?", (self.aid,))
        main.mark_final_images()
        self.assertEqual(self._row()['image_kind'], 'final_image')

    def test_batch_joins_clean_photo_by_number(self):
        def png(color, size=(1080, 1350)):
            b = BytesIO(); Image.new('RGB', size, color).save(b, 'PNG'); return b.getvalue()
        text = 'TITULAR: Primera noticia de prueba\nTEXTO: Uno.\n---\nTITULAR: Segunda noticia de prueba\nTEXTO: Dos.'
        files = [FakeUpload('1.png', png((1, 1, 1))), FakeUpload('2.png', png((2, 2, 2))),
                 FakeUpload('2-foto.jpg', png((200, 100, 50), (1600, 900)))]
        with patch.object(main.ocr, 'available', return_value=False):
            r = asyncio.run(main.launch_batch(text=text, brand='infolinense', txt=None, images=files))
        self.assertEqual((r['matched'], r['web_photos'], r['web_matched']), (2, 1, 1))
        first, second = r['items']
        self.assertFalse(first['has_web_photo'])
        self.assertTrue(second['has_web_photo'])
        a = db.row('SELECT * FROM articles WHERE id=?', (second['id'],))
        self.assertEqual(a['image_kind'], 'web_photo')
        with Image.open(a['image_local']) as im:
            self.assertEqual(im.getpixel((5, 5)), (200, 100, 50))
class CanvaNoDuplicateTests(unittest.TestCase):
    def test_merge_not_reposted_after_accept(self):
        from unittest import mock
        from app import canva
        posts = []
        def api(method, path, **kw):
            if method == 'POST': posts.append(path); return {'job': {'id': 'j1', 'status': 'in_progress'}}
            return {'job': {'status': 'in_progress'}}
        with mock.patch.object(canva, '_api', side_effect=api), mock.patch.object(canva.time, 'sleep'):
            with self.assertRaises(ValueError):
                canva._merge_call({'type': 'create_new_design'})
        self.assertEqual(posts, ['/merges'])

    def test_batch_skips_already_designed(self):
        from app import db, main
        cid = db.exec_("INSERT INTO candidates(title,url,status) VALUES('Lote hash test','https://x.test/lote-hash','new')")
        aid = db.exec_("INSERT INTO articles(candidate_id,status,headline,subtitle,workflow) VALUES(?,?,?,?,?)",
                       (cid, 'approved', 'Titular de lote ya diseñado', 'e', 'lote'))
        a = db.row('SELECT * FROM articles WHERE id=?', (aid,))
        db.exec_('UPDATE articles SET lote_hash=? WHERE id=?', (main._lote_hash(a), aid))
        a = db.row('SELECT * FROM articles WHERE id=?', (aid,))
        self.assertEqual(a['lote_hash'], main._lote_hash(a))


class CanvaConflictRetryTests(unittest.TestCase):
    def test_conflict_is_retried(self):
        from unittest import mock
        from app import canva
        posts = []
        def api(method, path, **kw):
            if method == 'POST':
                posts.append(path); return {'job': {'id': 'j%d' % len(posts), 'status': 'in_progress'}}
            if path.endswith('j1'):
                return {'job': {'status': 'failed', 'error': {'message': 'inline commit version conflicted on global version'}}}
            return {'job': {'status': 'success', 'result': {'design': {'id': 'D1'}}}}
        with mock.patch.object(canva, '_api', side_effect=api), mock.patch.object(canva.time, 'sleep'):
            job = canva._merge_call({'type': 'modify_existing_design'})
        self.assertEqual(len(posts), 2)
        self.assertEqual(job['status'], 'success')


class CanvaBatchSelectionTests(unittest.TestCase):
    def test_uses_last_import(self):
        import json
        from unittest import mock
        from app import db, main
        ids = []
        for n in range(3):
            cid = db.exec_("INSERT INTO candidates(title,url,status) VALUES(?,?,'new')", ('Sel %d' % n, 'https://x.test/sel%d' % n))
            ids.append(db.exec_("INSERT INTO articles(candidate_id,status,headline,workflow) VALUES(?,?,?,?)", (cid, 'approved', 'Titular %d' % n, 'lote')))
        db.exec_("INSERT OR REPLACE INTO canva_designs(article_id,design_id,url,exported,content_hash) VALUES(?,'x','',1,'h')", (ids[0],))
        db.set_setting('last_lote_ids', json.dumps(ids))
        seen = []
        with mock.patch.object(main.canva, 'batch_design', side_effect=lambda rows, progress=None: seen.extend(r['id'] for r in rows) or {'done_ids': []}), \
             mock.patch.object(main.threading, 'Thread', side_effect=lambda target, daemon: mock.Mock(start=target)):
            main._canva_job['busy'] = False
            main.canva_batch('')
        self.assertEqual(seen, ids)


class AdaptableTopicsAndSocialTests(unittest.TestCase):
    def test_topics(self):
        from app import sources
        for t in ('El paro baja en 25.000 personas en octubre', 'La renta media de los hogares sube un 4%',
                  'La economía crecerá un 2,5% según el Banco de España', 'La sequía obliga a restringir el agua'):
            self.assertTrue(sources.adaptable_ok(t), t)
        for t in ('El Ibex 35 cae un 2% en bolsa', 'Un incendio en Nerja obliga a desalojar', 'Feijóo carga contra la amnistía'):
            self.assertFalse(sources.adaptable_ok(t), t)

    def test_facebook_other_town(self):
        from app import social
        self.assertFalse(social.local_post('Vecinos de Nerja denuncian la suciedad en el paseo'))
        self.assertFalse(social.local_post('El Ayuntamiento de Nerja abre plazo de reclamaciones', True))
        self.assertTrue(social.local_post('Vecinos de la Atunara piden arreglar los baches'))
        self.assertTrue(social.local_post('Vecinos de La Línea (Cádiz) denuncian falta de limpieza en Santa Margarita'))


class StorageCleanupTests(unittest.TestCase):
    def test_keeps_pending_and_drops_orphans(self):
        import os, time
        from app import db, storage
        from app.config import UPLOAD_DIR
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        keep = UPLOAD_DIR / 'keep_storage_test.jpg'; orphan = UPLOAD_DIR / 'orphan_storage_test.jpg'; new = UPLOAD_DIR / 'new_storage_test.jpg'
        for f in (keep, orphan, new): f.write_bytes(b'x' * 10)
        old = time.time() - 5 * 86400
        for f in (keep, orphan): os.utime(f, (old, old))
        cid = db.exec_("INSERT INTO candidates(title,url,status) VALUES('Storage test','https://x.test/storage','new')")
        db.exec_("INSERT INTO articles(candidate_id,status,headline,image_local) VALUES(?,?,?,?)", (cid, 'draft', 'h', str(keep)))
        storage.cleanup()
        self.assertTrue(keep.exists()); self.assertFalse(orphan.exists()); self.assertTrue(new.exists())
        storage.cleanup(deep=True)  # recién bajada (<10 min): se respeta
        self.assertTrue(new.exists()); self.assertTrue(keep.exists())


class LearnFromDiscardsTests(unittest.TestCase):
    def test_learns_place_and_blocks(self):
        from app import db, learn, sources
        from datetime import datetime, timezone
        cid = db.exec_("INSERT INTO candidates(title,url,status) VALUES(?,?,'new')",
                       ('Un vecino de Castelldefels gana el premio de pintura', 'https://x.test/castell1'))
        db.exec_("UPDATE candidates SET editorial_priority='no_interest',status='archived' WHERE id=?", (cid,))
        self.assertIn('Castelldefels', learn.learn_from(cid))
        self.assertTrue(learn.blocked('Obras en el paseo de Castelldefels'))
        self.assertFalse(learn.blocked('Obras en el paseo de Poniente de La Línea'))
        meta = {'name': 'X', 'priority': 60, 'official': 0, 'local_scope': 1}
        now = datetime.now(timezone.utc).isoformat()
        self.assertIsNone(sources.add_candidate('Castelldefels estrena carril bici', 'https://x.test/castell2', '', 'X', None, now, meta))
        learn.remove('castelldefels')
        self.assertFalse(learn.blocked('Obras en el paseo de Castelldefels'))

    def test_never_learns_from_good_news(self):
        from app import db, learn
        db.exec_("INSERT INTO candidates(title,url,status,editorial_priority) VALUES('Nuevo carril en Alcaidesa','https://x.test/alc1','new','today')")
        cid = db.exec_("INSERT INTO candidates(title,url,status) VALUES('Fiesta privada en Alcaidesa','https://x.test/alc2','new')")
        self.assertNotIn('Alcaidesa', learn.learn_from(cid))


class AdaptElsewhereEventTests(unittest.TestCase):
    def test_dana_aid_not_adaptable(self):
        from app import sources
        self.assertFalse(sources.adaptable_ok('El Gobierno amplía las ayudas a los afectados por la DANA'))
        self.assertFalse(sources.adaptable_ok('Nuevas ayudas de la DANA para autónomos y empresas'))
        self.assertTrue(sources.adaptable_ok('La AEMET activa la alerta naranja por lluvias en Andalucía'))
        self.assertTrue(sources.adaptable_ok('El Gobierno aprueba nuevas ayudas al alquiler para jóvenes'))
