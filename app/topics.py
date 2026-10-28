"""Temas que sigues («obras calle Real», «relleno Gibraltar»): las noticias nuevas que encajan se marcan con el tema
y llega un aviso al móvil. Así es fácil hacer la segunda parte de una noticia."""
import json
import re
import unicodedata

from . import db

_STOP = set('''para como este esta estos estas pero sobre entre desde hasta tras ante bajo segun durante mediante
tambien donde cuando porque aunque mientras todo toda todos todas otro otra otros otras mismo misma cada nuevo nueva
linea concepcion linense linenses ayuntamiento ciudad vecinos vecinas'''.split())


def _fold(text):
    text = unicodedata.normalize('NFD', str(text or '').lower())
    return ''.join(c for c in text if unicodedata.category(c) != 'Mn')


def _ensure():
    db.exec_('''CREATE TABLE IF NOT EXISTS topics(id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL, terms TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    db.exec_('''CREATE TABLE IF NOT EXISTS topic_hits(topic_id INTEGER NOT NULL, candidate_id INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(topic_id,candidate_id))''')


def suggest(title):
    """Palabras clave propuestas desde un titular: nombres propios primero y luego palabras largas."""
    words = re.findall(r"[\wáéíóúñü]{4,}", title or '')
    proper = [w for i, w in enumerate(words) if w[0].isupper() and i > 0 and _fold(w) not in _STOP]
    rest = [w for w in words if not w[0].isupper() and _fold(w) not in _STOP and len(w) >= 6]
    out = []
    for w in proper + rest:
        if _fold(w) not in [_fold(x) for x in out]:
            out.append(w)
    return out[:3]


def _terms(raw):
    parts = raw if isinstance(raw, list) else re.split(r'[,;]+', str(raw or ''))
    return [p.strip() for p in parts if len(p.strip()) >= 3][:6]


def add(label, terms):
    _ensure()
    terms = _terms(terms) or _terms(label)
    if not terms:
        raise ValueError('Escribe al menos una palabra clave')
    tid = db.exec_('INSERT INTO topics(label,terms) VALUES(?,?)', ((label or ', '.join(terms)).strip()[:80], json.dumps(terms)))
    # las que ya hay en el panel también se marcan
    for c in db.rows("SELECT id,title,excerpt FROM candidates WHERE status NOT IN ('archived','merged') AND created_at>=datetime('now','-30 day')"):
        if _match(terms, (c['title'] or '') + ' ' + (c['excerpt'] or '')[:500]):
            db.exec_('INSERT OR IGNORE INTO topic_hits(topic_id,candidate_id) VALUES(?,?)', (tid, c['id']))
    return tid


def remove(tid):
    _ensure()
    db.exec_('UPDATE topics SET active=0 WHERE id=?', (tid,))


def _match(terms, text):
    t = _fold(text)
    found = sum(1 for w in terms if re.search(r'\b%s' % re.escape(_fold(w)), t))
    return found >= min(2, len(terms))


def check(candidate_id):
    """Al entrar una noticia nueva: si encaja en un tema, se marca y se avisa."""
    _ensure()
    c = db.row('SELECT id,title,excerpt FROM candidates WHERE id=?', (candidate_id,))
    if not c:
        return []
    hits = []
    for t in db.rows('SELECT * FROM topics WHERE active=1'):
        if _match(json.loads(t['terms']), (c['title'] or '') + ' ' + (c['excerpt'] or '')[:500]):
            db.exec_('INSERT OR IGNORE INTO topic_hits(topic_id,candidate_id) VALUES(?,?)', (t['id'], c['id']))
            hits.append(t['label'])
            try:
                from . import alerts
                alerts.notify('topic', 'Novedad en «%s»' % t['label'], c['title'] or '', '/#sort', key='topic:%s:%s' % (t['id'], c['id']))
            except Exception as exc:
                db.log('alert_error', str(exc)[:200])
    return hits


def listing():
    _ensure()
    out = []
    for t in db.rows('SELECT * FROM topics WHERE active=1 ORDER BY created_at DESC'):
        items = db.rows('''SELECT c.id,c.title,c.url,c.source_name,c.outlet,c.status,c.created_at,a.id article_id,a.publish_url
                           FROM topic_hits h JOIN candidates c ON c.id=h.candidate_id
                           LEFT JOIN articles a ON a.candidate_id=c.id AND a.status!='rejected'
                           WHERE h.topic_id=? ORDER BY c.created_at DESC LIMIT 30''', (t['id'],))
        out.append({'id': t['id'], 'label': t['label'], 'terms': json.loads(t['terms']), 'items': items,
                    'new': sum(1 for i in items if i['status'] not in ('published', 'archived'))})
    return out


def labels_for(candidate_ids):
    """{candidate_id: [tema, …]} para marcar las tarjetas."""
    _ensure()
    if not candidate_ids:
        return {}
    marks = ','.join('?' * len(candidate_ids))
    out = {}
    for r in db.rows('''SELECT h.candidate_id,t.label FROM topic_hits h JOIN topics t ON t.id=h.topic_id
                        WHERE t.active=1 AND h.candidate_id IN (%s)''' % marks, tuple(candidate_ids)):
        out.setdefault(r['candidate_id'], []).append(r['label'])
    return out
