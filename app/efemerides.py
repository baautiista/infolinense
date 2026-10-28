"""Efemérides Linenses: archivo histórico de La Línea de la Concepción que crece solo, con fuentes y estado de verificación.

De dónde salen (de más fiable y gratis a menos):
1. Wikidata: personas nacidas o fallecidas en La Línea, entidades y edificios con fecha de fundación o inauguración,
   acontecimientos con fecha. Con imagen de Wikimedia Commons y su licencia.
2. Wikipedia en español: frases con fecha completa de las páginas de historia de la ciudad (con la cita literal).
3. Investigación con IA y búsqueda web (solo si el proveedor busca de verdad en internet), en lotes pequeños con tope
   mensual. Sin enlace a la fuente, no se guarda nada.
Nunca se inventa una efeméride para rellenar un día: lo dudoso queda «pendiente de contrastar».
Las fuentes se guardan para verificar, pero no van al texto publicable.
"""
import json
import re
import time
import unicodedata
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import requests

from . import db

TZ = ZoneInfo('Europe/Madrid')
UA = {'User-Agent': 'InfoLinenseDesk/1.0 (archivo historico local; alertas@infolinense.com)'}
CATEGORIES = ['Inauguraciones', 'Primeras piedras', 'Fallecimientos', 'Nacimientos', 'Fundaciones', 'Urbanismo',
              'Acontecimientos históricos', 'Cultura y tradiciones']
STATUSES = {'verificada': 'Verificada', 'pendiente': 'Pendiente de contrastar', 'aproximada': 'Fecha aproximada', 'descartada': 'Descartada'}
EDITORIAL = {'nueva': 'Nueva', 'guardada': 'Guardada', 'propuesta': 'Propuesta de noticia', 'calendario': 'En el calendario',
             'carrusel': 'Para carrusel', 'publicada': 'Publicada'}
ROUND = (25, 50, 75, 100, 125, 150, 175, 200, 250)
MONTHLY_AI = 600
DAILY_AI = 20
MONTHS = {'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
          'septiembre': 9, 'setiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12}
WIKI_PAGES = ['La Línea de la Concepción', 'Historia de La Línea de la Concepción', 'Real Balompédica Linense',
              'Feria de La Línea de la Concepción', 'Carnaval de La Línea de la Concepción', 'Semana Santa en La Línea de la Concepción',
              'Verja de Gibraltar', 'Fuerte de Santa Bárbara (La Línea de la Concepción)', 'Museo Cruz Herrera']
PLACE = '?place rdfs:label "La Línea de la Concepción"@es ; wdt:P31 wd:Q2074737 .'


def _fold(text):
    text = unicodedata.normalize('NFD', str(text or '').lower())
    return ''.join(c for c in text if unicodedata.category(c) != 'Mn')


def ensure():
    db.exec_('''CREATE TABLE IF NOT EXISTS efemerides(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT,
                event_date TEXT, year INTEGER, month INTEGER, day INTEGER, precision TEXT, category TEXT, place TEXT, people TEXT,
                image_url TEXT, image_source TEXT, image_license TEXT, status TEXT NOT NULL DEFAULT 'pendiente',
                editorial TEXT NOT NULL DEFAULT 'nueva', dedup_key TEXT UNIQUE, origin TEXT, wikidata TEXT, candidate_id INTEGER,
                last_review TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    db.exec_('''CREATE TABLE IF NOT EXISTS efemeride_sources(efemeride_id INTEGER NOT NULL, name TEXT, url TEXT, quote TEXT,
                PRIMARY KEY(efemeride_id,url))''')
    db.exec_('''CREATE TABLE IF NOT EXISTS efemeride_runs(source_key TEXT PRIMARY KEY, done_at TEXT, items INTEGER, note TEXT)''')


# ---------- guardar sin duplicados ----------

def _key(title, year, month, day):
    words = sorted(w for w in re.findall(r'[a-z0-9ñ]{4,}', _fold(title)) if w not in ('linea', 'concepcion', 'nace', 'fallece'))[:6]
    return '%s-%s-%s|%s' % (year or 0, month or 0, day or 0, ' '.join(words))


def _similar_existing(title, year, month, day):
    from . import sources
    for r in db.rows('SELECT id,title FROM efemerides WHERE year=? AND COALESCE(month,0) IN (?,0) AND COALESCE(day,0) IN (?,0)',
                     (year, month or 0, day or 0)):
        if sources.same_story(title, r['title']):
            return r['id']
    return None


def save(title, year, month=0, day=0, category='', description='', place='', people='', sources_list=None, status=None,
         origin='', wikidata='', image=None):
    """Guarda (o une a la que ya hay) una efeméride. Devuelve (id, nueva)."""
    ensure()
    title = re.sub(r'\s+', ' ', title or '').strip()[:200]
    if not title or not year:
        return None, False
    month, day = int(month or 0), int(day or 0)
    precision = 'exacta' if month and day else 'mes' if month else 'año'
    status = status or ('pendiente' if precision == 'exacta' else 'aproximada')
    sources_list = [s for s in (sources_list or []) if s.get('url')]
    key = _key(title, year, month, day)
    eid = None
    row = db.row('SELECT id FROM efemerides WHERE dedup_key=? OR (wikidata!=\'\' AND wikidata=?)', (key, wikidata or '-'))
    if row:
        eid = row['id']
    else:
        eid = _similar_existing(title, year, month, day)
    new = False
    if not eid:
        eid = db.exec_('''INSERT INTO efemerides(title,description,event_date,year,month,day,precision,category,place,people,status,dedup_key,
                          origin,wikidata,image_url,image_source,image_license,last_review) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)''',
                       (title, (description or '')[:1500], '%04d-%02d-%02d' % (int(year), month, day), int(year), month, day, precision,
                        category if category in CATEGORIES else 'Acontecimientos históricos', place[:120], people[:300], status, key,
                        origin, wikidata or '', (image or {}).get('url', ''), (image or {}).get('source', ''), (image or {}).get('license', '')))
        new = True
    for s in sources_list:
        db.exec_('INSERT OR IGNORE INTO efemeride_sources(efemeride_id,name,url,quote) VALUES(?,?,?,?)',
                 (eid, (s.get('name') or '')[:120], s['url'][:500], (s.get('quote') or '')[:600]))
    _recheck(eid)
    return eid, new


def _recheck(eid):
    """Con dos o más fuentes independientes y fecha exacta, pasa a verificada (salvo que la hayas descartado)."""
    e = db.row('SELECT * FROM efemerides WHERE id=?', (eid,))
    if not e or e['status'] in ('descartada', 'verificada'):
        return
    hosts = {re.sub(r'^www\.', '', (re.match(r'https?://([^/]+)', s['url']) or [None, ''])[1])
             for s in db.rows('SELECT url FROM efemeride_sources WHERE efemeride_id=?', (eid,))}
    hosts.discard('')
    if e['precision'] == 'exacta' and len(hosts) >= 2:
        db.exec_("UPDATE efemerides SET status='verificada',last_review=CURRENT_TIMESTAMP WHERE id=?", (eid,))


# ---------- 1. Wikidata ----------

def _sparql(q):
    r = requests.get('https://query.wikidata.org/sparql', params={'query': q, 'format': 'json'}, timeout=90,
                     headers=dict(UA, Accept='application/sparql-results+json'))
    r.raise_for_status()
    return r.json().get('results', {}).get('bindings', [])


def _wd_date(value, prec):
    m = re.match(r'[+]?(-?\d{1,4})-(\d{2})-(\d{2})', value or '')
    if not m:
        return 0, 0, 0
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    prec = int(prec or 9)
    return y, (mo if prec >= 10 else 0), (d if prec >= 11 else 0)


def _commons(files):
    """Licencia y autor de imágenes de Wikimedia Commons."""
    out = {}
    names = [f for f in files if f]
    for i in range(0, len(names), 40):
        chunk = names[i:i + 40]
        try:
            r = requests.get('https://commons.wikimedia.org/w/api.php', headers=UA, timeout=40, params={
                'action': 'query', 'format': 'json', 'prop': 'imageinfo', 'iiprop': 'url|extmetadata', 'iiurlwidth': 800,
                'titles': '|'.join('File:' + n for n in chunk)})
            for p in (r.json().get('query', {}).get('pages') or {}).values():
                info = (p.get('imageinfo') or [{}])[0]
                meta = info.get('extmetadata') or {}
                lic = (meta.get('LicenseShortName') or {}).get('value', '')
                artist = re.sub(r'<[^>]+>', '', (meta.get('Artist') or {}).get('value', ''))[:120]
                out[p.get('title', '').replace('File:', '')] = {'url': info.get('thumburl') or info.get('url') or '',
                                                                 'source': 'Wikimedia Commons' + (' · ' + artist if artist else ''),
                                                                 'license': lic}
        except Exception:
            continue
    return out


def harvest_wikidata():
    ensure()
    queries = {
        'nacidos': ('Nacimientos', 'Nace %s', 'SELECT ?x ?xLabel ?xDescription ?d ?p ?img ?art WHERE { %s ?x wdt:P19 ?place ; p:P569/psv:P569 [ wikibase:timeValue ?d ; wikibase:timePrecision ?p ] . OPTIONAL { ?x wdt:P18 ?img } OPTIONAL { ?art schema:about ?x ; schema:isPartOf <https://es.wikipedia.org/> } SERVICE wikibase:label { bd:serviceParam wikibase:language "es,en". } }' % PLACE),
        'fallecidos_aqui': ('Fallecimientos', 'Fallece %s', 'SELECT ?x ?xLabel ?xDescription ?d ?p ?img ?art WHERE { %s ?x wdt:P20 ?place ; p:P570/psv:P570 [ wikibase:timeValue ?d ; wikibase:timePrecision ?p ] . OPTIONAL { ?x wdt:P18 ?img } OPTIONAL { ?art schema:about ?x ; schema:isPartOf <https://es.wikipedia.org/> } SERVICE wikibase:label { bd:serviceParam wikibase:language "es,en". } }' % PLACE),
        'fallecidos_linenses': ('Fallecimientos', 'Fallece %s', 'SELECT ?x ?xLabel ?xDescription ?d ?p ?img ?art WHERE { %s ?x wdt:P19 ?place ; p:P570/psv:P570 [ wikibase:timeValue ?d ; wikibase:timePrecision ?p ] . OPTIONAL { ?x wdt:P18 ?img } OPTIONAL { ?art schema:about ?x ; schema:isPartOf <https://es.wikipedia.org/> } SERVICE wikibase:label { bd:serviceParam wikibase:language "es,en". } }' % PLACE),
        'fundaciones': ('Fundaciones', 'Se funda %s', 'SELECT ?x ?xLabel ?xDescription ?d ?p ?img ?art WHERE { %s { ?x wdt:P131 ?place } UNION { ?x wdt:P159 ?place } ?x p:P571/psv:P571 [ wikibase:timeValue ?d ; wikibase:timePrecision ?p ] . OPTIONAL { ?x wdt:P18 ?img } OPTIONAL { ?art schema:about ?x ; schema:isPartOf <https://es.wikipedia.org/> } SERVICE wikibase:label { bd:serviceParam wikibase:language "es,en". } }' % PLACE),
        'inauguraciones': ('Inauguraciones', 'Se inaugura %s', 'SELECT ?x ?xLabel ?xDescription ?d ?p ?img ?art WHERE { %s ?x wdt:P131 ?place ; p:P1619/psv:P1619 [ wikibase:timeValue ?d ; wikibase:timePrecision ?p ] . OPTIONAL { ?x wdt:P18 ?img } OPTIONAL { ?art schema:about ?x ; schema:isPartOf <https://es.wikipedia.org/> } SERVICE wikibase:label { bd:serviceParam wikibase:language "es,en". } }' % PLACE),
        'acontecimientos': ('Acontecimientos históricos', '%s', 'SELECT ?x ?xLabel ?xDescription ?d ?p ?img ?art WHERE { %s ?x wdt:P276 ?place ; p:P585/psv:P585 [ wikibase:timeValue ?d ; wikibase:timePrecision ?p ] . OPTIONAL { ?x wdt:P18 ?img } OPTIONAL { ?art schema:about ?x ; schema:isPartOf <https://es.wikipedia.org/> } SERVICE wikibase:label { bd:serviceParam wikibase:language "es,en". } }' % PLACE),
    }
    total = 0
    for key, (cat, pattern, q) in queries.items():
        try:
            rows = _sparql(q)
        except Exception as exc:
            db.log('efemerides_error', ('Wikidata %s: %s' % (key, exc))[:250])
            continue
        imgs = _commons([re.sub(r'.*/Special:FilePath/', '', requests.utils.unquote(b['img']['value'])) for b in rows if b.get('img')])
        for b in rows:
            name = b.get('xLabel', {}).get('value', '')
            if not name or re.fullmatch(r'Q\d+', name):
                continue
            y, mo, d = _wd_date(b.get('d', {}).get('value'), b.get('p', {}).get('value'))
            if not y or y < 1700 or y > datetime.now(TZ).year:
                continue
            qid = b['x']['value'].rsplit('/', 1)[-1]
            art = b.get('art', {}).get('value', '')
            srcs = [{'name': 'Wikidata', 'url': b['x']['value'], 'quote': ''}] + ([{'name': 'Wikipedia', 'url': art, 'quote': ''}] if art else [])
            fname = re.sub(r'.*/Special:FilePath/', '', requests.utils.unquote(b['img']['value'])) if b.get('img') else ''
            status = 'verificada' if (mo and d and art) else ('pendiente' if mo and d else 'aproximada')
            eid, new = save(pattern % name, y, mo, d, cat, b.get('xDescription', {}).get('value', ''), 'La Línea de la Concepción',
                            name if cat in ('Nacimientos', 'Fallecimientos') else '', srcs, status, 'wikidata', qid + ':' + key, imgs.get(fname))
            total += 1 if new else 0
    db.exec_('INSERT OR REPLACE INTO efemeride_runs(source_key,done_at,items,note) VALUES(?,CURRENT_TIMESTAMP,?,?)', ('wikidata', total, ''))
    return total


# ---------- 2. Wikipedia ----------

_DATE_SENT = re.compile(r'\b(?:el\s+)?(\d{1,2})\s+de\s+(%s)\s+de\s+(1[6-9]\d\d|20[0-2]\d)\b' % '|'.join(MONTHS), re.I)
_CAT_HINTS = [('Inauguraciones', r'inaugur'), ('Primeras piedras', r'primera\s+piedra'), ('Fundaciones', r'fund[aó]|se\s+crea|constitu'),
              ('Fallecimientos', r'falleci|muri[oó]'), ('Nacimientos', r'naci[oó]'), ('Urbanismo', r'calle|barriada|demoli|urbaniz|ensanche|plaza'),
              ('Cultura y tradiciones', r'feria|carnaval|semana\s+santa|velada|balomp[eé]dica|hermandad|cofrad')]


def harvest_wikipedia():
    ensure()
    total = 0
    for page in WIKI_PAGES:
        try:
            r = requests.get('https://es.wikipedia.org/w/api.php', headers=UA, timeout=40, params={
                'action': 'query', 'format': 'json', 'prop': 'extracts|revisions', 'explaintext': 1, 'rvprop': 'ids', 'titles': page, 'redirects': 1})
            pages = list((r.json().get('query', {}).get('pages') or {}).values())
        except Exception as exc:
            db.log('efemerides_error', ('Wikipedia %s: %s' % (page, exc))[:250])
            continue
        if not pages or 'missing' in pages[0]:
            continue
        p = pages[0]
        rev = str(((p.get('revisions') or [{}])[0]).get('revid') or '')
        key = 'wikipedia:' + page
        done = db.row('SELECT note FROM efemeride_runs WHERE source_key=?', (key,))
        if done and done.get('note') == rev:
            continue  # sin cambios desde la última vez: no se vuelve a procesar
        url = 'https://es.wikipedia.org/wiki/' + p.get('title', page).replace(' ', '_')
        n = 0
        for sent in re.split(r'(?<=[.!?])\s+', p.get('extract') or ''):
            m = _DATE_SENT.search(sent)
            if not m or len(sent) > 400:
                continue
            y, mo, d = int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1))
            cat = next((c for c, rx in _CAT_HINTS if re.search(rx, sent, re.I)), 'Acontecimientos históricos')
            title = re.sub(r'^\s*(?:el\s+)?\d{1,2}\s+de\s+\w+\s+de\s+\d{4},?\s*', '', sent).strip()
            title = (title[:1].upper() + title[1:])[:160] if title else sent[:160]
            eid, new = save(title, y, mo, d, cat, sent, 'La Línea de la Concepción', '', [{'name': 'Wikipedia', 'url': url, 'quote': sent}],
                            'pendiente', 'wikipedia')
            n += 1 if new else 0
        db.exec_('INSERT OR REPLACE INTO efemeride_runs(source_key,done_at,items,note) VALUES(?,CURRENT_TIMESTAMP,?,?)', (key, n, rev))
        total += n
    return total


# ---------- 3. Investigación con IA (búsqueda web) ----------

TOPICS = [(cat, decade) for decade in range(1870, 2030, 10) for cat in CATEGORIES]


def ai_budget():
    month = datetime.now(TZ).strftime('%Y-%m')
    used = int(db.get_setting('efem_ai_' + month, '0') or 0)
    today = int(db.get_setting('efem_ai_day_' + datetime.now(TZ).strftime('%Y-%m-%d'), '0') or 0)
    return used, today


def _spend():
    month, day = datetime.now(TZ).strftime('%Y-%m'), datetime.now(TZ).strftime('%Y-%m-%d')
    db.set_setting('efem_ai_' + month, str(int(db.get_setting('efem_ai_' + month, '0') or 0) + 1))
    db.set_setting('efem_ai_day_' + day, str(int(db.get_setting('efem_ai_day_' + day, '0') or 0) + 1))


def research(limit=5):
    """Lote de investigación con IA: cada consulta, un tema y una década (en orden, sin repetir lo ya hecho)."""
    from . import ai
    ensure()
    if not ai.AI_ENABLED or not any(ai.web_enabled(p) for p in ai.provider_chain()):
        return {'added': 0, 'note': 'Sin IA con búsqueda web: la investigación automática con IA está parada (Wikidata y Wikipedia siguen).'}
    added, asked = 0, 0
    for cat, decade in TOPICS:
        used, today = ai_budget()
        if asked >= limit or used >= MONTHLY_AI or today >= DAILY_AI:
            break
        key = 'ia:%s:%s' % (cat, decade)
        if db.row('SELECT 1 FROM efemeride_runs WHERE source_key=?', (key,)):
            continue
        prompt = ('Investiga en internet acontecimientos DOCUMENTADOS de La Línea de la Concepción (Cádiz) de tipo «%s» entre %d y %d. '
                  'Busca en hemerotecas (Biblioteca Nacional, Prensa Histórica), webs de historia local, asociaciones, hermandades, archivos y prensa. '
                  'Solo hechos con fecha y con una fuente real que hayas visto (URL) y una cita breve de ella. No inventes nada; si no encuentras, '
                  'devuelve la lista vacía. JSON {"items":[{"titulo","descripcion","anio":1950,"mes":0,"dia":0,"lugar","personas",'
                  '"fuentes":[{"nombre","url","cita"}]}]} (mes y día 0 si no se conocen; máximo 8).') % (cat, decade, decade + 9)
        asked += 1
        _spend()
        try:
            data, _, used_web = ai.ask_json(ai.SYSTEM, prompt, web=True, web_required=True, max_tokens=2500)
        except Exception as exc:
            db.log('efemerides_error', ('IA %s: %s' % (key, exc))[:250])
            continue
        n = 0
        for it in (data or {}).get('items') or []:
            srcs = [{'name': s.get('nombre') or '', 'url': s.get('url') or '', 'quote': s.get('cita') or ''}
                    for s in it.get('fuentes') or [] if isinstance(s, dict) and str(s.get('url') or '').startswith('http')]
            try:
                y = int(it.get('anio') or 0)
            except (TypeError, ValueError):
                y = 0
            if not srcs or not (decade - 5 <= y <= decade + 15):
                continue  # sin fuente o fuera del periodo: no se guarda
            eid, new = save(it.get('titulo') or '', y, it.get('mes') or 0, it.get('dia') or 0, cat, it.get('descripcion') or '',
                            it.get('lugar') or '', it.get('personas') or '', srcs, None, 'ia')
            n += 1 if new else 0
        added += n
        db.exec_('INSERT OR REPLACE INTO efemeride_runs(source_key,done_at,items,note) VALUES(?,CURRENT_TIMESTAMP,?,?)', (key, n, ''))
    return {'added': added, 'asked': asked}


def daily():
    """Una vez al día: Wikidata y Wikipedia cada semana, IA en lote pequeño, y aviso de aniversarios redondos."""
    ensure()
    today = datetime.now(TZ).date().isoformat()
    if db.get_setting('efem_daily', '') == today:
        return
    db.set_setting('efem_daily', today)
    last = db.row("SELECT done_at FROM efemeride_runs WHERE source_key='wikidata'")
    if not last or (datetime.now(timezone.utc) - datetime.fromisoformat(last['done_at'].replace(' ', 'T') + '+00:00')).days >= 7:
        harvest_wikidata()
        harvest_wikipedia()
    research(limit=DAILY_AI)
    try:
        from . import alerts
        for e in upcoming(7):
            if e.get('round') and e['status'] != 'descartada':
                alerts.notify('agenda', 'Efeméride: %d años de %s' % (e['years'], e['title'][:80]), 'El %d/%d' % (e['day'], e['month']),
                              '/#efemerides', key='efem:%s:%s' % (e['id'], datetime.now(TZ).year))
    except Exception as exc:
        db.log('alert_error', str(exc)[:200])


# ---------- consultas para la pantalla ----------

def _decorate(rows, year=None):
    year = year or datetime.now(TZ).year
    ids = [r['id'] for r in rows]
    srcs = {}
    if ids:
        for s in db.rows('SELECT * FROM efemeride_sources WHERE efemeride_id IN (%s)' % ','.join('?' * len(ids)), tuple(ids)):
            srcs.setdefault(s['efemeride_id'], []).append(s)
    for r in rows:
        r['years'] = year - (r['year'] or year)
        r['round'] = r['years'] in ROUND
        r['sources'] = srcs.get(r['id'], [])
    return rows


def search(q='', category='', status='', period='', month=0, day=0):
    ensure()
    sql, args = 'SELECT * FROM efemerides WHERE 1=1', []
    if status:
        sql += ' AND status=?'; args.append(status)
    else:
        sql += " AND status!='descartada'"
    if category:
        sql += ' AND category=?'; args.append(category)
    if month:
        sql += ' AND month=?'; args.append(int(month))
    if day:
        sql += ' AND day=?'; args.append(int(day))
    if period and re.fullmatch(r'\d{4}-\d{4}', period):
        a, b = period.split('-'); sql += ' AND year BETWEEN ? AND ?'; args += [int(a), int(b)]
    rows = db.rows(sql + ' ORDER BY month,day,year LIMIT 1000', tuple(args))
    if q:
        words = _fold(q).split()
        rows = [r for r in rows if all(w in _fold(' '.join(str(r.get(k) or '') for k in ('title', 'description', 'people', 'place', 'year'))) for w in words)]
    return _decorate(rows[:400])


def on_day(month, day):
    ensure()
    return _decorate(db.rows("SELECT * FROM efemerides WHERE month=? AND day=? AND status!='descartada' ORDER BY year", (month, day)))


def upcoming(days=7, start=None):
    from datetime import timedelta
    start = start or datetime.now(TZ).date()
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        for r in on_day(d.month, d.day):
            r['next_date'] = d.isoformat()
            out.append(r)
    return out


def calendar(year, month=0):
    """Número de efemérides por día del mes (o por mes del año)."""
    ensure()
    if month:
        rows = db.rows("SELECT day,COUNT(*) n,SUM(CASE WHEN ?-year IN (%s) THEN 1 ELSE 0 END) r FROM efemerides WHERE month=? AND day>0 AND status!='descartada' GROUP BY day"
                       % ','.join(str(x) for x in ROUND), (year, month))
        return {str(r['day']): {'n': r['n'], 'round': r['r'] or 0} for r in rows}
    rows = db.rows("SELECT month,COUNT(*) n FROM efemerides WHERE month>0 AND status!='descartada' GROUP BY month")
    return {str(r['month']): {'n': r['n']} for r in rows}


def stats():
    ensure()
    used, today = ai_budget()
    by = {r['status']: r['n'] for r in db.rows('SELECT status,COUNT(*) n FROM efemerides GROUP BY status')}
    runs = db.rows('SELECT * FROM efemeride_runs ORDER BY done_at DESC LIMIT 5')
    return {'by_status': by, 'total': sum(by.values()), 'ai_month': used, 'ai_month_max': MONTHLY_AI, 'ai_today': today,
            'ai_day_max': DAILY_AI, 'runs': runs}


def next_anniversary(e):
    today = datetime.now(TZ).date()
    if not (e.get('month') and e.get('day')):
        return None
    for y in (today.year, today.year + 1):
        try:
            d = date(y, e['month'], e['day'])
        except ValueError:
            d = date(y, e['month'], 28)
        if d >= today:
            return d
    return None
