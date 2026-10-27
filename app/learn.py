"""Aprende de lo que descartas («No sirve», Eliminar, Retirar) para no volver a enseñarlo.

1. Nombres propios del titular que nunca salen en noticias que sí te sirven (p. ej. «Castelldefels»):
   las noticias nuevas que los traigan no entran.
2. Titulares casi iguales a uno descartado (la misma noticia desde otro medio) tampoco entran.
Desde Ajustes se ven y se pueden quitar.
"""
import re
import time
import unicodedata

from . import db

# Palabras con mayúscula que salen en cualquier noticia: nunca se aprenden.
_COMMON = set('''la el los las un una unos unas de del en y o con por para al a su sus se lo este esta estos estas ese esa
linea linense linenses concepcion gibraltar gibraltareno gibraltarena espana espanol espanola andalucia andaluz andaluza junta gobierno
ayuntamiento campo cadiz diputacion estado europa union europea ue policia guardia civil local nacional municipal provincial
semana santa navidad carnaval feria velada hermandad cofradia virgen cristo senor nuestra padre san santa santo
enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre
lunes martes miercoles jueves viernes sabado domingo hoy manana ayer
psoe pp vox sumar iu adelante ciudadanos podemos alcalde alcaldesa concejal concejala delegado delegada presidente presidenta ministro ministra
consejero consejera seguridad social sanidad salud educacion empleo trabajo hacienda economia ine aemet dgt sepe boe boja bop
real club deportivo cd ud fc balompedica balona plaza calle avenida barriada parque playa poniente levante centro norte sur este oeste
asi como cuando donde que quien cual mas menos muy nuevo nueva nuevos nuevas gran grande primer primera ultimo ultima'''.split())
_SINCE = time.time()
_cache = {'at': 0.0, 'terms': {}, 'titles': []}


def _fold(text):
    text = unicodedata.normalize('NFD', str(text or '').lower())
    return ''.join(c for c in text if unicodedata.category(c) != 'Mn')


def _ensure():
    db.exec_('''CREATE TABLE IF NOT EXISTS learned_terms(term TEXT PRIMARY KEY, label TEXT, hits INTEGER NOT NULL DEFAULT 1,
                example TEXT, active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')


def proper_nouns(title):
    """Nombres propios del titular (sin la primera palabra, que siempre va en mayúscula)."""
    words = re.findall(r"[A-ZÁÉÍÓÚÑÜ][\wáéíóúñüàèòç'·-]{3,}", title or '')
    first = re.match(r"\s*[\W\d]*([\wáéíóúñüÁÉÍÓÚÑÜ'·-]+)", title or '')
    out = []
    for i, w in enumerate(words):
        if i == 0 and first and first.group(1) == w:
            continue
        f = _fold(w)
        if f in _COMMON or f.isdigit() or len(f) < 4:
            continue
        out.append((f, w))
    return out


def _content(text):
    return {w for w in re.findall(r'[a-z0-9ñ]{4,}', _fold(text)) if w not in _COMMON}


def _in_good_news(term):
    """¿Sale en noticias que sí te sirvieron? Entonces no se aprende (no se bloquea lo que te interesa)."""
    like = '%' + term + '%'
    return bool(db.row("""SELECT 1 FROM candidates WHERE (editorial_priority IN ('urgent','today','this_week','future') OR status='published')
                          AND (lower(title) LIKE ? OR lower(title) LIKE ?) LIMIT 1""", (like, '%' + term.capitalize() + '%')))


def learn_from(cid):
    """Se llama al descartar una tarjeta: guarda sus nombres propios que nunca salen en lo que te sirve."""
    _ensure()
    c = db.row('SELECT title FROM candidates WHERE id=?', (cid,))
    if not c:
        return []
    learned = []
    for term, label in proper_nouns(c['title']):
        if _in_good_news(term):
            continue
        db.exec_('''INSERT INTO learned_terms(term,label,hits,example) VALUES(?,?,1,?)
                    ON CONFLICT(term) DO UPDATE SET hits=hits+1,example=excluded.example''', (term, label, c['title'][:200]))
        learned.append(label)
    _cache['at'] = 0
    return learned


def _load():
    if time.time() - _cache['at'] < 120:
        return _cache
    _ensure()
    _cache['terms'] = {r['term']: r['label'] for r in db.rows('SELECT term,label FROM learned_terms WHERE active=1')}
    _cache['titles'] = [(_content(r['title']), r['title']) for r in db.rows(
        """SELECT title FROM candidates WHERE editorial_priority='no_interest' AND created_at>=datetime('now','-90 day')
           ORDER BY id DESC LIMIT 1500""")]
    _cache['at'] = time.time()
    return _cache


def blocked(title, excerpt=''):
    """Motivo si se parece a lo que descartas, o ''."""
    data = _load()
    head = _fold(title)
    for term, label in data['terms'].items():
        if re.search(r'\b%s\b' % re.escape(term), head):
            return 'Aprendido: descartas noticias de «%s»' % label
    words = _content(title)
    if len(words) >= 4:
        for other, old in data['titles']:
            if other and len(words & other) / len(words | other) >= 0.55:
                return 'Aprendido: igual a una que descartaste («%s»)' % old[:80]
    return ''


def terms():
    _ensure()
    return db.rows('SELECT term,label,hits,example,created_at FROM learned_terms WHERE active=1 ORDER BY created_at DESC')


def add(label):
    _ensure()
    term = _fold(label).strip()
    if len(term) < 3:
        raise ValueError('Escribe una palabra o un sitio')
    db.exec_('''INSERT INTO learned_terms(term,label,hits,example,active) VALUES(?,?,1,'Añadido a mano',1)
                ON CONFLICT(term) DO UPDATE SET active=1''', (term, label.strip()))
    _cache['at'] = 0


def remove(term):
    _ensure()
    db.exec_('UPDATE learned_terms SET active=0 WHERE term=?', (_fold(term),))
    _cache['at'] = 0
