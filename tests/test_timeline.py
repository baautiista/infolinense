import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

_temp = tempfile.TemporaryDirectory()
os.environ.setdefault('DATA_DIR', _temp.name)
os.environ.setdefault('DB_PATH', str(Path(_temp.name) / 'timeline.sqlite'))
for _k, _v in (('RENDER_DIR', 'renders'), ('UPLOAD_DIR', 'uploads'), ('TEMPLATE_DIR', 'templates')):
    os.environ.setdefault(_k, str(Path(_temp.name) / _v))
    Path(os.environ[_k]).mkdir(parents=True, exist_ok=True)

from fastapi import HTTPException  # noqa: E402
from app import ai, db, main  # noqa: E402


class TimelineTests(unittest.TestCase):
    def setUp(self):
        db.init_db()
        for t in ('articles', 'candidates'):
            db.exec_('DELETE FROM ' + t)
        self.today = datetime.now(main.MADRID).date()
        self.cid = db.exec_("INSERT INTO candidates(title,url,status,editorial_priority,planned_at) VALUES('x','https://x/1','draft','today',?)",
                            (self.today.isoformat() + 'T09:00+02:00',))
        self.aid = db.exec_("INSERT INTO articles(candidate_id,headline,subtitle,body,status,time_ref) VALUES(?,?,?,?,?,?)",
                            (self.cid, 'Hoy se inaugura la feria', 'Abre esta tarde', 'La feria abre hoy a las 18.00.', 'draft', self.today.isoformat()))

    def test_move_same_day_changes_slot_without_ai(self):
        with patch.object(ai, 'ask_json', side_effect=AssertionError('no hace falta IA')):
            r = main.move_article(self.aid, main.MoveIn(date=self.today.isoformat(), slot='night'))
        self.assertFalse(r['retimed'])
        self.assertTrue(db.row('SELECT planned_at FROM candidates WHERE id=?', (self.cid,))['planned_at'].endswith('T21:00+02:00')
                        or 'T21:00' in db.row('SELECT planned_at FROM candidates WHERE id=?', (self.cid,))['planned_at'])

    def test_move_to_tomorrow_rewrites_time_words(self):
        tomorrow = self.today + timedelta(days=1)
        seen = {}

        def fake(system, prompt, **kw):
            seen['prompt'] = prompt
            return {'headline': 'Ayer se inauguró la feria', 'subtitle': 'Abrió ayer por la tarde', 'body': 'La feria abrió ayer a las 18.00.'}, 'gemini', False
        with patch.object(ai, 'AI_ENABLED', True), patch.object(ai, 'ask_json', side_effect=fake):
            r = main.move_article(self.aid, main.MoveIn(date=tomorrow.isoformat(), slot='morning'))
        self.assertTrue(r['retimed'])
        a = db.row('SELECT headline,time_ref FROM articles WHERE id=?', (self.aid,))
        self.assertEqual((a['headline'], a['time_ref']), ('Ayer se inauguró la feria', tomorrow.isoformat()))
        self.assertIn(ai._fecha(tomorrow), seen['prompt'])
        self.assertEqual(db.row('SELECT editorial_priority FROM candidates WHERE id=?', (self.cid,))['editorial_priority'], 'this_week')

    def test_no_time_words_no_ai(self):
        db.exec_("UPDATE articles SET headline='Nueva rotonda en la avenida',subtitle='',body='Obras de mejora.' WHERE id=?", (self.aid,))
        with patch.object(ai, 'ask_json', side_effect=AssertionError('no hace falta IA')):
            r = main.move_article(self.aid, main.MoveIn(date=(self.today + timedelta(days=3)).isoformat(), slot='afternoon'))
        self.assertFalse(r['retimed'])

    def test_delete(self):
        main.delete_article(self.aid)
        self.assertEqual(db.row('SELECT status FROM articles WHERE id=?', (self.aid,))['status'], 'rejected')
        self.assertEqual(db.row('SELECT editorial_priority FROM candidates WHERE id=?', (self.cid,))['editorial_priority'], 'no_interest')
        with self.assertRaises(HTTPException):
            main.move_article(self.aid, main.MoveIn(date=self.today.isoformat(), slot='bad'))


if __name__ == '__main__':
    unittest.main()
