import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

_temp = tempfile.TemporaryDirectory()
os.environ.setdefault('DATA_DIR', _temp.name)
os.environ.setdefault('DB_PATH', str(Path(_temp.name) / 'sched.sqlite'))
for _k, _v in (('RENDER_DIR', 'renders'), ('UPLOAD_DIR', 'uploads'), ('TEMPLATE_DIR', 'templates')):
    os.environ.setdefault(_k, str(Path(_temp.name) / _v))
    Path(os.environ[_k]).mkdir(parents=True, exist_ok=True)

from PIL import Image  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from app import db, main, sources, social_publish as sp  # noqa: E402
from app.config import RENDER_DIR, UPLOAD_DIR  # noqa: E402

NOW = datetime.now(timezone.utc).isoformat()


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        db.init_db(); sp.init_tables()
        for t in ('social_posts', 'articles', 'candidates'):
            db.exec_('DELETE FROM ' + t)
        Path(RENDER_DIR).mkdir(parents=True, exist_ok=True); Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
        photo = Path(UPLOAD_DIR) / 'p.jpg'; Image.new('RGB', (50, 50)).save(photo)
        cid = db.exec_("INSERT INTO candidates(title,url,status) VALUES('x','https://x/1','review_ready')")
        self.aid = db.exec_("INSERT INTO articles(candidate_id,headline,subtitle,body,status,image_local) VALUES(?,?,?,?,?,?)",
                            (cid, 'Titular', 'Entradilla', 'Texto', 'review_ready', str(photo)))

    def test_schedule_then_publish_when_due(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).astimezone(main.MADRID).strftime('%Y-%m-%dT%H:%M')
        r = main.schedule_article(self.aid, main.ScheduleIn(at=future, networks=[]))
        self.assertTrue(r['scheduled_at'].startswith(future))
        with patch.object(main.publishers, 'publish', side_effect=AssertionError('aún no')):
            self.assertEqual(main.publish_due(), [])  # todavía no es la hora
        db.exec_("UPDATE articles SET scheduled_at=? WHERE id=?", ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), self.aid))
        with patch.object(main.publishers, 'AUTO_PUBLISH', True), patch.object(main.publishers, 'PUBLISH_MODE', 'lovable'), \
                patch.object(main.publishers, 'publish', return_value='https://web/x'):
            self.assertEqual(main.publish_due(), [self.aid])
        a = db.row('SELECT status,publish_url,scheduled_at FROM articles WHERE id=?', (self.aid,))
        self.assertEqual((a['status'], a['publish_url'], a['scheduled_at']), ('published', 'https://web/x', None))

    def test_past_time_and_missing_canva_are_rejected(self):
        with self.assertRaises(HTTPException):
            main.schedule_article(self.aid, main.ScheduleIn(at='2020-01-01T10:00'))
        future = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%dT10:00')
        with patch.object(sp, 'connected_networks', return_value=['instagram']), self.assertRaises(HTTPException) as e:
            main.schedule_article(self.aid, main.ScheduleIn(at=future, networks=['instagram']))
        self.assertIn('Canva', str(e.exception.detail))

    def test_failed_scheduled_publish_is_reported(self):
        db.exec_("UPDATE articles SET scheduled_at=?,scheduled_networks='[]' WHERE id=?", (NOW, self.aid))
        with patch.object(main.publishers, 'AUTO_PUBLISH', True), patch.object(main.publishers, 'PUBLISH_MODE', 'lovable'), \
                patch.object(main.publishers, 'publish', side_effect=RuntimeError('web caída')):
            main.publish_due()
        a = db.row('SELECT status,scheduled_at,schedule_error FROM articles WHERE id=?', (self.aid,))
        self.assertIsNone(a['scheduled_at']); self.assertIn('web caída', a['schedule_error'])


class OnlyLaLineaTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        for t in ('candidate_links', 'articles', 'candidates'):
            db.exec_('DELETE FROM ' + t)

    def add(self, title, excerpt='', local=0, name='Google News'):
        return sources.add_candidate(title, 'https://n/' + str(abs(hash(title))), excerpt, name, published_at=NOW,
                                     source_meta={'priority': 70, 'official': 0, 'local_scope': local})

    def test_other_towns_out_gibraltar_only_relevant(self):
        self.assertIsNone(self.add('Algeciras aprueba su presupuesto municipal para el próximo año'))
        self.assertIsNone(self.add('San Roque abre la nueva piscina cubierta en Taraguilla', local=1))
        self.assertIsNone(self.add('Detenido un narcotraficante en el Campo de Gibraltar con 300 kilos'))
        self.assertIsNone(self.add('Gibraltar: el ministro de Sanidad presenta su informe anual'))
        self.assertTrue(self.add('Gibraltar inicia el relleno de terrenos ganados al mar en la bahía oeste'))
        self.assertTrue(self.add('Gibraltar celebrará elecciones generales el 15 de noviembre'))
        self.assertTrue(self.add('El Ayuntamiento de La Línea arregla la calle Real'))

    def test_tenders_only_from_la_linea(self):
        self.assertIsNone(self.add('Licitación: Ayuntamiento de Algeciras. Servicio de limpieza de playas'))
        self.assertTrue(self.add('Licitación: obras del paseo marítimo de Poniente', 'Ayuntamiento de La Línea de la Concepción'))

    def test_existing_off_topic_cards_are_archived(self):
        cid = db.exec_("INSERT INTO candidates(source_name,title,url,status,published_at) VALUES(?,?,?,?,?)",
                       ('Google News', 'Algeciras inaugura su feria de octubre', 'https://n/alg', 'new', NOW))
        self.assertEqual(sources.archive_off_topic(), 1)
        self.assertEqual(db.row('SELECT status FROM candidates WHERE id=?', (cid,))['status'], 'archived')

    def test_bop_reads_only_its_announcement(self):
        from reportlab.pdfgen import canvas
        buf = BytesIO(); c = canvas.Canvas(buf)
        for page in ['332.452.- Area de Funcion Publica. Otra cosa de Jerez.',
                     '317.900.- Ayuntamiento de Jerez. Nombramientos.',
                     '318.111.- Ayuntamiento de La Linea de la Concepcion. Aprobacion inicial del Plan de Poniente.',
                     '319.222.- Ayuntamiento de Rota. Tasas.']:
            c.drawString(50, 800, page); c.showPage()
        c.save()

        class R:
            content = buf.getvalue(); headers = {'content-type': 'application/pdf'}; text = ''
            def raise_for_status(self): pass
        with patch.object(sources, 'fetch', return_value=R()):
            text = sources.fetch_article_text('https://bopcadiz.es/x/BOP.pdf#page=2&anuncio=318111')
        self.assertIn('Plan de Poniente', text)
        self.assertNotIn('Rota', text); self.assertNotIn('Jerez', text)


if __name__ == '__main__':
    unittest.main()
