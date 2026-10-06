import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

_temp = tempfile.TemporaryDirectory()
os.environ.setdefault('DATA_DIR', _temp.name)
os.environ.setdefault('DB_PATH', str(Path(_temp.name) / 'grid.sqlite'))
for _k, _v in (('RENDER_DIR', 'renders'), ('UPLOAD_DIR', 'uploads'), ('TEMPLATE_DIR', 'templates')):
    os.environ.setdefault(_k, str(Path(_temp.name) / _v))
    Path(os.environ[_k]).mkdir(parents=True, exist_ok=True)

from app import db, planner  # noqa: E402

FIXED = datetime(2026, 10, 7, 7, 0, tzinfo=planner.TZ)


class FakeDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED


class GridTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        for t in ('articles', 'candidates'):
            db.exec_('DELETE FROM ' + t)
        self.ayto = db.row("SELECT id FROM sources WHERE url='https://lalinea.es/feed/'")['id']

    def add(self, title, prio='today', section='', source=None, brand='infolinense'):
        return db.exec_("INSERT INTO candidates(title,url,status,editorial_priority,section,source_id,published_at,brand,score) VALUES(?,?,?,?,?,?,?,?,?)",
                        (title, 'https://x/' + title, 'new', prio, section, source, FIXED.isoformat(), brand, 60))

    def plan(self):
        with patch.object(planner, 'datetime', FakeDT):
            planner.rebuild_schedule()
        return {r['title']: datetime.fromisoformat(r['planned_at']) for r in db.rows('SELECT title,planned_at FROM candidates WHERE planned_at IS NOT NULL')}

    def test_daily_limit_and_content(self):
        self.add('Pleno municipal aprueba el presupuesto', section='POLÍTICA', source=self.ayto)
        self.add('El Ayuntamiento abre el plazo de ayudas al alquiler', section='SOCIEDAD', source=self.ayto)
        self.add('Torneo juvenil de fútbol este fin de semana', section='DEPORTES')
        self.add('Concierto de jóvenes bandas en el parque', section='CULTURA')
        for i in range(12):
            self.add(f'Obras en la calle número {i} del barrio {i}', section='OBRAS')
        plan = self.plan()
        today = [t for t, d in plan.items() if d.date() == FIXED.date()]
        self.assertLessEqual(len(today), len(planner.GRID))  # nunca más que la parrilla
        self.assertLess(plan['Pleno municipal aprueba el presupuesto'].hour, 14)  # Ayuntamiento por la mañana
        self.assertLess(plan['El Ayuntamiento abre el plazo de ayudas al alquiler'].hour, 14)
        self.assertGreaterEqual(plan['Torneo juvenil de fútbol este fin de semana'].hour, 16)  # juvenil, tarde-noche
        self.assertGreaterEqual(plan['Concierto de jóvenes bandas en el parque'].hour, 16)
        self.assertTrue(any(d.date() > FIXED.date() for d in plan.values()))  # lo que no cabe, al día siguiente
        # no dos de la misma sección seguidas hoy, si hay alternativa
        order = sorted((d, t) for t, d in plan.items() if d.date() == FIXED.date())
        secs = [db.row('SELECT section FROM candidates WHERE title=?', (t,))['section'] for _, t in order]
        self.assertLess(sum(1 for a, b in zip(secs, secs[1:]) if a == b), len(secs) - 1)

    def test_brands_have_their_own_grid(self):
        for i in range(9):
            self.add(f'Noticia general número {i} sobre la ciudad', section='CIUDAD')
        self.add('Besamanos de la Amargura este domingo', brand='cofrade', section='Misericordia y Amargura')
        plan = self.plan()
        self.assertEqual(plan['Besamanos de la Amargura este domingo'].date(), FIXED.date())


if __name__ == '__main__':
    unittest.main()
