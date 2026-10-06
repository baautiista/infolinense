import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_temp = tempfile.TemporaryDirectory()
os.environ.setdefault('DATA_DIR', _temp.name)
os.environ.setdefault('DB_PATH', str(Path(_temp.name) / 'merge.sqlite'))
os.environ.setdefault('RENDER_DIR', str(Path(_temp.name) / 'renders'))
os.environ.setdefault('UPLOAD_DIR', str(Path(_temp.name) / 'uploads'))
os.environ.setdefault('TEMPLATE_DIR', str(Path(_temp.name) / 'templates'))
for _k in ('RENDER_DIR', 'UPLOAD_DIR', 'TEMPLATE_DIR'):
    Path(os.environ[_k]).mkdir(parents=True, exist_ok=True)

from datetime import datetime, timezone  # noqa: E402
from app import db, main, sources  # noqa: E402

NOW = datetime.now(timezone.utc).isoformat()
LOCAL = {'priority': 80, 'official': 0, 'local_scope': 1}


class Resp:
    def __init__(self, text, status=200, url='https://www.sedeelectronica.lalinea.es/'):
        self.text, self.status_code, self.ok, self.url = text, status, status < 400, url

    def raise_for_status(self):
        if not self.ok: raise RuntimeError('HTTP %s' % self.status_code)


class MergeTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        for t in ('candidate_links', 'articles', 'candidates'):
            db.exec_('DELETE FROM ' + t)

    def test_same_story_from_two_sources_is_one_card(self):
        a = sources.add_candidate('La Policía Local retira 19 patinetes en La Línea', 'https://europasur.es/a', 'Texto A',
                                  'Europa Sur', published_at=NOW, source_meta=LOCAL, outlet='Europa Sur')
        b = sources.add_candidate('Retirados 19 patinetes eléctricos por la Policía Local en La Línea - Diario Área',
                                  'https://diarioarea.com/b', 'Texto B', 'Prensa comarcal', published_at=NOW, source_meta=LOCAL, outlet='Diario Área')
        self.assertTrue(a); self.assertIsNone(b)
        rows = main.candidates('pending')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['links'][0]['outlet'], 'Diario Área')
        # la misma URL no vuelve a entrar
        self.assertIsNone(sources.add_candidate('Otra cosa distinta en La Línea de la Concepción', 'https://diarioarea.com/b', '', 'x',
                                                published_at=NOW, source_meta=LOCAL))

    def test_different_stories_and_edictos_are_not_merged(self):
        sources.add_candidate('El Ayuntamiento de La Línea abre la piscina municipal', 'https://a.es/1', '', 'A', published_at=NOW, source_meta=LOCAL)
        sources.add_candidate('El Ayuntamiento de La Línea cierra el mercado municipal', 'https://a.es/2', '', 'A', published_at=NOW, source_meta=LOCAL)
        sources.add_candidate('Edicto: licencia de obras en calle Real 12 de La Línea', 'https://sede/1', '', 'Tablón', published_at=NOW,
                              source_meta=dict(LOCAL, kind='edictos'))
        sources.add_candidate('Edicto: licencia de obras en calle Real 14 de La Línea', 'https://sede/2', '', 'Tablón', published_at=NOW,
                              source_meta=dict(LOCAL, kind='edictos'))
        self.assertEqual(len(main.candidates('pending')), 4)

    def test_existing_duplicates_are_merged_keeping_the_chosen_one(self):
        ids = [db.exec_("INSERT INTO candidates(source_name,outlet,title,url,score,status,published_at,editorial_priority) VALUES(?,?,?,?,?,?,?,?)",
                        (n, n, t, u, 60, 'new', NOW, p))
               for n, t, u, p in [('Europa Sur', 'Incendio en una vivienda de San Bernardo en La Línea', 'https://e/1', 'undecided'),
                                  ('Área', 'Un incendio calcina una vivienda en la barriada de San Bernardo', 'https://e/2', 'today')]]
        self.assertEqual(sources.merge_duplicates(), 1)
        kept = db.row('SELECT * FROM candidates WHERE id=?', (ids[1],))  # la que ya estaba marcada para hoy se queda
        self.assertEqual(kept['status'], 'new')
        self.assertEqual(db.row('SELECT status,merged_into FROM candidates WHERE id=?', (ids[0],)), {'status': 'merged', 'merged_into': ids[1]})
        self.assertEqual(sources.links_for([ids[1]])[ids[1]][0]['url'], 'https://e/1')

    def test_edictos_gets_a_session_first(self):
        today = datetime.now().strftime('%d/%m/%Y')
        table = ('<table><tr><td>%s 09:00</td><td>%s</td><td>Aprobación inicial de la modificación del PGOU en la zona de Poniente</td>'
                 '<td><a href="ver-edicto;jsessionid=AB12?codigo=555">Ver</a></td></tr></table>' % (today, today))
        calls = []

        def fake_get(self, url, **kw):
            calls.append(url)
            if 'buscar-edictos' in url:
                return Resp(table) if len(calls) > 1 else Resp('ERROR 403: La sesión ha expirado, para acceder vuelva a iniciar sesión', 403)
            return Resp('<script>location.href="/edictos/edicto/inicio"</script>')

        with patch.object(sources.requests.Session, 'get', fake_get), patch.object(sources.time, 'sleep', lambda s: None):
            items = sources.parse_edictos({'url': 'https://www.sedeelectronica.lalinea.es/edictos/edicto/buscar-edictos-filtro-pub?primeraBusqueda=true'})
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]['title'].startswith('Edicto: Aprobación inicial'))
        self.assertEqual(items[0]['url'], 'https://www.sedeelectronica.lalinea.es/edictos/edicto/ver-edicto?codigo=555')
        self.assertEqual(calls[0], 'https://www.sedeelectronica.lalinea.es/edictos/publico?idOrgan=23')  # entrada pública primero

    def test_edictos_public_entry_is_enough(self):
        today = datetime.now().strftime('%d/%m/%Y')
        table = ('<table><tr><td>%s</td><td>31/01/2099</td><td>BASES PUEBLO NAVIDEÑO NAVIDAD Y REYES</td>'
                 '<td><a href="/edictos/edicto/descarga.action;jsessionid=X?codigo=2026-179987">Ver</a></td></tr></table>' % today)
        with patch.object(sources.requests.Session, 'get', lambda self, url, **kw: Resp(table)):
            items = sources.parse_edictos({'url': 'https://www.sedeelectronica.lalinea.es/edictos/edicto/buscar-edictos-filtro-pub?primeraBusqueda=true'})
        self.assertEqual(items[0]['url'], 'https://www.sedeelectronica.lalinea.es/edictos/edicto/descarga.action?codigo=2026-179987')

    def test_edictos_source_is_active(self):
        s = db.row("SELECT * FROM sources WHERE kind='edictos'")
        self.assertEqual((s['active'], s['priority']), (1, 99))


if __name__ == '__main__':
    unittest.main()


class ProcurementSources(unittest.TestCase):
    def setUp(self):
        db.init_db()
        for t in ('candidate_links', 'articles', 'candidates'):
            db.exec_('DELETE FROM ' + t)

    def test_licitacionesio_cards_and_same_tender_merged(self):
        today = datetime.now().strftime('%d/%m/%Y')
        later = '15/12/2099'
        html = f'''<div class="card"><h3><a href="/licitacion/licitacion-de-exhibicion-con-drones-luminosos-museo-cruz-63e1">Licitación de exhibición con drones luminosos Museo Cruz Herrera</a></h3>
          <p>Ayuntamiento de La Línea de la Concepción</p><span>Publicada {today}</span><span>Plazo {later}</span><span>14.876,04 €</span></div>'''

        class R:
            text = html; status_code = 200; ok = True
            def raise_for_status(self): pass
        with patch.object(sources, 'fetch', return_value=R()):
            items = sources.read_source({'url': 'https://licitaciones.io/licitaciones/la-linea-de-la-concepcion-ciudad', 'kind': 'rss'})
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]['url'].startswith('https://licitaciones.io/licitacion/'))
        self.assertIn('Plazo hasta 15/12/2099', items[0]['excerpt'])
        a = sources.add_candidate('Licitación: Exhibición drones luminosos con motivo del XI Aniversario del Museo Cruz Herrera',
                                  'https://contratos.gobierto.es/licitaciones/5230345', '', 'Gobierto', published_at=NOW,
                                  source_meta={'priority': 97, 'official': 0, 'local_scope': 1, 'kind': 'procurement'})
        b = sources.add_candidate(items[0]['title'], items[0]['url'], items[0]['excerpt'], 'Licitaciones io', published_at=NOW,
                                  source_meta={'priority': 80, 'official': 0, 'local_scope': 1})
        self.assertTrue(a); self.assertIsNone(b)
        self.assertEqual(sources.links_for([a])[a][0]['url'], items[0]['url'])

    def test_rss_source_that_is_a_web_page_falls_back(self):
        class R:
            text = '<html><body><article><h2><a href="/n/1">El Ayuntamiento de La Línea abre el plazo de matrícula</a></h2></article></body></html>'
            content = text.encode(); status_code = 200; ok = True
            def raise_for_status(self): pass
        with patch.object(sources, 'fetch', return_value=R()):
            items = sources.read_source({'url': 'https://example.es/', 'kind': 'rss'})
        self.assertEqual(items[0]['url'], 'https://example.es/n/1')
