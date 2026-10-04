"""Botón Redactar, proveedor de IA, titulares y agenda."""
import os
import json
import asyncio
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

_temp = tempfile.TemporaryDirectory()
os.environ.setdefault('DATA_DIR', _temp.name)
os.environ.setdefault('DB_PATH', str(Path(_temp.name) / 'writing.sqlite'))
os.environ.setdefault('RENDER_DIR', str(Path(_temp.name) / 'renders'))
os.environ.setdefault('UPLOAD_DIR', str(Path(_temp.name) / 'uploads'))
os.environ.setdefault('TEMPLATE_DIR', str(Path(_temp.name) / 'templates'))
os.environ['AUTO_DRAFT_USEFUL'] = 'false'  # solo la prueba de AutoDraft la activa

from app import ai, db, pipeline, planner, main  # noqa: E402
from fastapi import HTTPException  # noqa: E402

DRAFT = {
    'section': 'CIUDAD', 'headline': 'El Ayuntamiento abre el plazo de ayudas al alquiler',
    'subtitle': 'Las solicitudes se presentan hasta el 30 de octubre en la sede electrónica.',
    'body': 'Texto de prueba. ' * 100, 'graphic_summary': 'Ayudas al alquiler',
    'headline_options': ['Titular %d' % i for i in range(7)], 'missing_data': ['Importe total'],
    'carousel_suitable': True, 'carousel_reason': 'Requisitos', 'provider': 'anthropic',
}


class Resp:
    def __init__(self, status, payload, headers=None):
        self.status_code = status
        self._payload = payload
        self.ok = 200 <= status < 300
        self.headers = headers or {}

    def json(self):
        return self._payload


def new_candidate(title):
    return db.exec_("INSERT INTO candidates(source_name,title,url,score,status) VALUES(?,?,?,?,?)",
                    ('Ayuntamiento', title, 'https://lalinea.es/' + str(time.time_ns()), 80, 'new'))


class WriteButton(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def setUp(self):
        self.p = [patch.object(pipeline.sources, 'fetch_article_text', return_value='Texto fuente'),
                  patch.object(pipeline.photos, 'search_real_photos', return_value=[]),
                  patch.object(ai, 'AI_ENABLED', True)]
        for x in self.p: x.start()

    def tearDown(self):
        for x in self.p: x.stop()

    def test_new_story_is_researched_drafted_then_opened_without_duplicates(self):
        cid = new_candidate('Ayudas al alquiler en La Línea')
        calls = []
        with patch.object(ai, 'research', side_effect=lambda c, t: calls.append('r') or {'facts': ['x'], 'sources': []}), \
             patch.object(ai, 'draft', side_effect=lambda *a, **k: calls.append('d') or dict(DRAFT)):
            first = main.write_candidate(cid, wait=20)
            second = main.write_candidate(cid, wait=20)
        self.assertEqual(first['action'], 'drafted')
        self.assertEqual(second['action'], 'opened')
        self.assertEqual(first['article']['id'], second['article']['id'])
        self.assertEqual(calls, ['r', 'd'])
        self.assertEqual(db.row('SELECT COUNT(*) n FROM articles WHERE candidate_id=?', (cid,))['n'], 1)
        art = first['article']
        self.assertEqual(len(json.loads(art['headline_options_json'])), 7)
        self.assertLessEqual(len(art['body']), 2200)
        self.assertEqual(main.write_status(cid)['state'], 'done')

    def test_researched_story_only_drafts(self):
        cid = new_candidate('Edicto de obras en La Línea')
        db.exec_("UPDATE candidates SET status='researched',research_json='{}' WHERE id=?", (cid,))
        with patch.object(ai, 'research', side_effect=AssertionError('no debe investigar')), \
             patch.object(ai, 'draft', return_value=dict(DRAFT)):
            self.assertEqual(main.write_candidate(cid, wait=20)['action'], 'drafted')

    def test_double_click_creates_a_single_draft(self):
        cid = new_candidate('Licitación del alumbrado de La Línea')
        gate = threading.Event()

        def slow(*a, **k):
            gate.wait(3)
            return dict(DRAFT)
        with patch.object(ai, 'research', return_value={'facts': []}), patch.object(ai, 'draft', side_effect=slow):
            first = main.write_candidate(cid, wait=0)
            self.assertEqual(first.status_code, 202)
            again = main.write_candidate(cid, wait=0)
            self.assertEqual(again.status_code, 202)
            self.assertEqual(main.write_status(cid)['state'], 'working')
            gate.set()
            for _ in range(50):
                if main.write_status(cid)['state'] == 'done': break
                time.sleep(0.05)
        self.assertEqual(main.write_status(cid)['state'], 'done')
        self.assertEqual(db.row('SELECT COUNT(*) n FROM articles WHERE candidate_id=?', (cid,))['n'], 1)

    def test_provider_error_is_reported_and_retry_works(self):
        cid = new_candidate('Cortes de agua en La Línea')
        err = ai.AIProviderError('La cuenta API no tiene saldo.', 'openai', 429, 'insufficient_quota', 'quota')
        with patch.object(ai, 'research', return_value={'facts': []}), patch.object(ai, 'draft', side_effect=err):
            with self.assertRaises(ai.AIProviderError):
                main.write_candidate(cid, wait=20)
        status = main.write_status(cid)
        self.assertEqual(status['state'], 'error')
        self.assertIn('insufficient_quota', status['error']['detail'])
        response = asyncio.run(main.ai_error_handler(None, err))
        body = json.loads(response.body)
        self.assertIn('HTTP 429', body['detail'])
        self.assertEqual(body['error']['kind'], 'quota')
        with patch.object(ai, 'research', return_value={'facts': []}), patch.object(ai, 'draft', return_value=dict(DRAFT)):
            self.assertEqual(main.write_candidate(cid, wait=20)['action'], 'drafted')


class Providers(unittest.TestCase):
    def test_claude_is_used_with_x_api_key_and_openai_is_fallback(self):
        seen = []

        def fake_post(url, headers=None, json=None, timeout=None):
            seen.append((url, headers))
            if 'anthropic' in url:
                return Resp(400, {'error': {'type': 'invalid_request_error', 'message': 'Your credit balance is too low'}})
            return Resp(200, {'output_text': '{"ok": true}'})
        with patch.object(ai, 'ACTIVE_PROVIDER', 'anthropic'), patch.object(ai, 'ANTHROPIC_API_KEY', 'sk-ant-x'), \
             patch.object(ai, 'OPENAI_API_KEY', 'sk-x'), patch.object(ai, 'AI_FALLBACK', True), \
             patch.object(ai.requests, 'post', side_effect=fake_post):
            text, provider, _ = ai.ask_ai('s', 'u')
        self.assertEqual(provider, 'openai')
        self.assertEqual(seen[0][1]['x-api-key'], 'sk-ant-x')
        self.assertNotIn('Authorization', seen[0][1])

    def test_openai_rate_limit_and_quota_are_distinguished(self):
        rate = ai._openai_error(Resp(429, {'error': {'code': 'rate_limit_exceeded', 'message': 'slow down'}}, {'retry-after': '20'}))
        quota = ai._openai_error(Resp(429, {'error': {'code': 'insufficient_quota', 'type': 'insufficient_quota'}}))
        self.assertEqual((rate.kind, rate.retry_after), ('rate_limit', 20))
        self.assertEqual(quota.kind, 'quota')

    def test_both_errors_are_shown_when_fallback_also_fails(self):
        def fake_post(url, headers=None, json=None, timeout=None):
            if 'anthropic' in url:
                return Resp(529, {'error': {'type': 'overloaded_error', 'message': 'Overloaded'}})
            return Resp(429, {'error': {'code': 'insufficient_quota'}})
        with patch.object(ai, 'ACTIVE_PROVIDER', 'anthropic'), patch.object(ai, 'ANTHROPIC_API_KEY', 'k'), \
             patch.object(ai, 'OPENAI_API_KEY', 'k'), patch.object(ai, 'AI_FALLBACK', True), \
             patch.object(ai.requests, 'post', side_effect=fake_post):
            with self.assertRaises(ai.AIProviderError) as ctx:
                ai.ask_ai('s', 'u')
        text = main.ai_error_text(ctx.exception)
        self.assertIn('Claude', text)
        self.assertIn('insufficient_quota', text)

    def test_headlines_need_exactly_seven(self):
        reply = {'headlines': ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']}
        with patch.object(ai, 'AI_ENABLED', True), patch.object(ai, 'ask_json', return_value=(reply, 'anthropic', False)):
            self.assertEqual(ai.alternate_headlines({'headline': 'Z'}), ['A', 'B', 'C', 'D', 'E', 'F', 'G'])


class Agenda(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def test_manual_time_is_kept_when_new_stories_arrive(self):
        db.exec_("UPDATE candidates SET editorial_priority='undecided'")
        manual = new_candidate('Pleno municipal de La Línea')
        tomorrow = (datetime.now(planner.TZ) + timedelta(days=1)).replace(hour=12, minute=15, second=0, microsecond=0)
        main.triage_candidate(manual, main.TriageIn(priority='this_week', planned_at=tomorrow.isoformat()))
        others = [new_candidate('Edicto %d en La Línea' % i) for i in range(4)]
        for cid in others:
            main.triage_candidate(cid, main.TriageIn(priority='this_week'))
        row = db.row('SELECT planned_at,plan_locked FROM candidates WHERE id=?', (manual,))
        self.assertEqual(row['plan_locked'], 1)
        self.assertTrue(row['planned_at'].startswith(tomorrow.strftime('%Y-%m-%dT12:15')))
        slots = [db.row('SELECT planned_at FROM candidates WHERE id=?', (c,))['planned_at'] for c in others]
        self.assertTrue(all(slots))
        self.assertEqual(len(set(slots)), len(slots))

    def test_invalid_priority_is_rejected(self):
        with self.assertRaises(HTTPException):
            main.triage_candidate(new_candidate('Otra de La Línea'), main.TriageIn(priority='mañana'))


if __name__ == '__main__':
    unittest.main()


class TemplateLayout(unittest.TestCase):
    def test_long_headline_fits_four_lines_and_section_picks_its_page(self):
        from app import layout, canva
        long = 'Más de 550 toneladas de alga asiática retiradas en Poniente en lo que va de 2026'
        self.assertEqual(len(layout.headline_lines(long)), 4)  # medido en Canva a 74 px
        too_long = long + ' según el balance municipal presentado este lunes en el pleno'
        fitted = canva.design_fields({'headline': too_long, 'section': 'Playas', 'subtitle': 'x' * 400})
        self.assertTrue(layout.headline_fits(fitted['HEADLINE']))
        self.assertEqual((fitted['SECTION'], fitted['page']), ('MEDIO AMBIENTE', 8))
        self.assertLessEqual(len(fitted['SUMMARY']), layout.SUMMARY_MAX)
        self.assertEqual(layout.family_for('Fútbol')[1], 6)
        self.assertEqual(layout.family_for('AGENDA')[3], '#061E5C')


class AutoDraft(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def test_marking_a_story_useful_drafts_it_automatically(self):
        cid = new_candidate('Nueva línea de autobús en La Línea')
        with patch.object(pipeline.sources, 'fetch_article_text', return_value='Texto'), \
             patch.object(pipeline.photos, 'search_real_photos', return_value=[]), \
             patch.object(ai, 'research', return_value={'facts': []}), \
             patch.object(ai, 'draft', return_value=dict(DRAFT)), patch.object(pipeline, 'AUTO_DRAFT_USEFUL', True):
            main.triage_candidate(cid, main.TriageIn(priority='today'))
            for _ in range(100):
                if pipeline.existing_article(cid): break
                time.sleep(0.05)
        self.assertIsNotNone(pipeline.existing_article(cid))
        self.assertEqual(db.row('SELECT COUNT(*) n FROM articles WHERE candidate_id=?', (cid,))['n'], 1)

    def test_no_interest_is_not_drafted(self):
        cid = new_candidate('Otra noticia de La Línea')
        main.triage_candidate(cid, main.TriageIn(priority='no_interest'))
        time.sleep(0.2)
        self.assertIsNone(pipeline.existing_article(cid))


class WebPublish(unittest.TestCase):
    def test_lovable_receives_article_with_secret_and_photo(self):
        from app import publishers
        photo = Path(_temp.name) / 'pub.jpg'
        from PIL import Image
        Image.new('RGB', (800, 600), 'blue').save(photo)
        sent = {}

        def fake_post(url, json=None, headers=None, timeout=None):
            sent.update(url=url, json=json, headers=headers)
            return Resp(200, {'url': 'https://infolinense.com/noticia/x'})
        art = {'id': 7, 'headline': 'Más de 550 toneladas de alga', 'subtitle': 'E', 'body': 'Uno.\nDos.', 'section': 'Playas',
               'image_local': str(photo), 'sources_json': '[]', 'source_url': 'https://lalinea.es/a'}
        Resp.text = ''
        with patch.object(publishers, 'PUBLISH_MODE', 'lovable'), patch.object(publishers, 'LOVABLE_WEBHOOK_URL', 'https://x.supabase.co/functions/v1/publish-article'), \
             patch.object(publishers, 'PUBLISH_WEBHOOK_SECRET', 's3cret'), patch.object(publishers.requests, 'post', side_effect=fake_post):
            url = publishers.publish(art)
        self.assertEqual(url, 'https://infolinense.com/noticia/x')
        self.assertEqual(sent['headers']['x-infolinense-secret'], 's3cret')
        self.assertEqual(sent['json']['section'], 'MEDIO AMBIENTE')
        self.assertEqual(sent['json']['slug'], 'mas-de-550-toneladas-de-alga-7')
        self.assertTrue(sent['json']['image_base64'])
        self.assertEqual(sent['json']['body_html'], '<p>Uno.</p><p>Dos.</p>')


class SocialPanel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def test_facebook_complaints_are_found_and_classified(self):
        from app import social
        html = '''<div><div><a href="/url?q=https://www.facebook.com/groups/lalineavecinos/posts/123&sa=U"><h3>Vecinos de San Bernardo denuncian baches y suciedad en La Línea</h3></a>
                  <span>Hace 5 horas · Los vecinos se quejan de que nadie arregla los baches de la calle desde hace meses en La Línea.</span></div></div>
                  <div><div><a href="https://www.facebook.com/aytolalinea/posts/9"><h3>El Ayuntamiento de La Línea abre la piscina cubierta</h3></a><span>Horario de invierno en La Línea a partir del lunes y nuevas actividades.</span></div></div>
                  <a href="https://otra.web/x"><h3>Otra cosa</h3></a>'''

        class R:
            text = html
        with patch.object(social.requests, 'get', return_value=R()), patch.object(social.ai if hasattr(social, 'ai') else ai, 'provider_chain', return_value=[]):
            result = social.scan()
        rows = {r['url']: r for r in social.items()}
        self.assertTrue(result['added'])
        group_post = rows['https://www.facebook.com/groups/lalineavecinos/posts/123']
        self.assertEqual(group_post['social_type'], 'queja')
        self.assertEqual(rows['https://www.facebook.com/aytolalinea/posts/9']['social_type'], 'noticia')
        self.assertNotIn('https://otra.web/x', rows)


class NewsStyle(unittest.TestCase):
    def test_draft_never_talks_about_missing_data_or_sources(self):
        reply = dict(DRAFT, body='La piscina cubierta abre el lunes. Se desconoce dónde se venderán las entradas.\n\n'
                                 'El horario será de 8 a 22 horas. La documentación no especifica el precio.',
                     subtitle='Abre el lunes con horario ampliado. No consta el precio.')
        with patch.object(ai, 'AI_ENABLED', True), patch.object(ai, 'ask_json', return_value=(reply, 'openai', False)):
            d = ai.draft({'title': 'Piscina'}, 'texto', '{"facts":["abre el lunes"],"missing":["precio"]}')
        text = d['headline'] + d['subtitle'] + d['body']
        for bad in ('desconoce', 'documentación', 'No consta'):
            self.assertNotIn(bad, text)
        self.assertIn('8 a 22 horas', d['body'])
        self.assertEqual(d['missing_data'], [])

    def test_photos_come_from_the_whole_web(self):
        from app import photos

        class Page:
            text = 'vqd="4-123456789"&'

        class Results:
            def json(self):
                return {'results': [{'image': 'https://diario.es/fotos/playa-poniente.jpg', 'url': 'https://diario.es/n', 'width': 1600, 'height': 900},
                                    {'image': 'https://x.com/logo.png', 'url': 'https://x.com', 'width': 800, 'height': 800},
                                    {'image': 'https://y.es/mini.jpg', 'url': 'https://y.es', 'width': 120, 'height': 90}]}

        class FakeSession:
            headers = {}
            def get(self, url, **kw):
                return Results() if 'i.js' in url else Page()
        with patch.object(photos.requests, 'Session', return_value=FakeSession()):
            found = photos.duckduckgo_images('playa Poniente La Línea')
        self.assertEqual([f['url'] for f in found], ['https://diario.es/fotos/playa-poniente.jpg'])


class StoryImage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def test_rss_keeps_the_image_of_each_story_at_full_size(self):
        from app import sources, photos
        feed = '''<?xml version="1.0"?><rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><item>
          <title>La ciudad participará en la feria de turismo de Londres</title><link>https://lalinea.es/feria-londres/</link>
          <content:encoded><![CDATA[<p><img src="https://lalinea.es/wp-content/uploads/2026/10/Feria-de-Londres-300x175.jpg" width="300"
          srcset="https://lalinea.es/wp-content/uploads/2026/10/Feria-de-Londres-300x175.jpg 300w, https://lalinea.es/wp-content/uploads/2026/10/Feria-de-Londres.jpg 580w"></p>]]></content:encoded>
          </item></channel></rss>'''

        class R:
            content = feed.encode()
            def raise_for_status(self): pass
        with patch.object(sources, 'fetch', return_value=R()):
            items = sources.parse_rss({'url': 'https://lalinea.es/feed/'})
        self.assertEqual(items[0]['image'], 'https://lalinea.es/wp-content/uploads/2026/10/Feria-de-Londres.jpg')
        with patch.object(photos, 'page_images', return_value=[]), patch.object(photos, 'internet_images', return_value=[]) as web:
            found = photos.search_real_photos({'title': 'Más de 550 toneladas de alga asiática retiradas en Poniente', 'url': 'https://lalinea.es/a',
                                               'image_hint': items[0]['image']}, '', 'playa Poniente La Línea alga asiática')
        self.assertEqual(found[0]['url'], items[0]['image'])
        self.assertEqual(web.call_args_list[0][0][0], 'playa Poniente La Línea alga asiática')
