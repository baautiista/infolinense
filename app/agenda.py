"""Agenda de la ciudad: plenos, ferias, partidos, conciertos, plazos… Se sacan solos de las noticias que traen una
fecha futura y también se añaden a mano. Unos días antes llega un aviso para preparar la previa."""
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import db

TZ = ZoneInfo('Europe/Madrid')
MONTHS = {'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
          'septiembre': 9, 'setiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12}
DAYS = {'lunes': 0, 'martes': 1, 'miércoles': 2, 'miercoles': 2, 'jueves': 3, 'viernes': 4, 'sábado': 5, 'sabado': 5, 'domingo': 6}
KINDS = [('Pleno', r'pleno'), ('Deporte', r'partido|balona|torneo|campeonato|carrera|marat[oó]n|liga'),
         ('Fiesta', r'feria|velada|cabalgata|carnaval|procesi[oó]n|romer[ií]a|fiesta|verbena'),
         ('Cultura', r'concierto|festival|exposici[oó]n|teatro|cine|gala|presentaci[oó]n|recital|charla|conferencia|jornadas?|taller'),
         ('Plazo', r'plazo|inscripci[oó]n|convocatoria|solicitud(es)?|matr[ií]cula|ayudas?'),
         ('Servicio', r'corte\s+de|cierre|obras|huelga|manifestaci[oó]n|concentraci[oó]n|mercadillo|inauguraci[oó]n|apertura')]
_KIND_RX = re.compile('|'.join('(?:%s)' % rx for _, rx in KINDS), re.I)
_DATE = re.compile(r'\b(?:(lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\s*,?\s*)?(\d{1,2})\s+de\s+(%s)(?:\s+de\s+(\d{4}))?'
                   % '|'.join(MONTHS), re.I)
_WEEKDAY = re.compile(r'\b(?:este|el\s+pr[oó]ximo|pr[oó]ximo)\s+(lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\b', re.I)
_TIME = re.compile(r'\ba\s+las\s+(\d{1,2})(?:[:.](\d{2}))?\s*(?:h\b|horas)?', re.I)


def _ensure():
    db.exec_('''CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, day TEXT NOT NULL,
                time TEXT, place TEXT, kind TEXT, url TEXT, candidate_id INTEGER, manual INTEGER NOT NULL DEFAULT 0,
                reminded INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')


def kind_of(text):
    for name, rx in KINDS:
        if re.search(r'\b(' + rx + r')', text or '', re.I):
            return name
    return ''


def find_date(text, ref=None):
    """Primera fecha futura (hasta 90 días) del texto: «5 de noviembre», «el próximo sábado»… y hora si la hay."""
    ref = ref or datetime.now(TZ).date()
    text = text or ''
    best = None
    for m in _DATE.finditer(text):
        try:
            y = int(m.group(4)) if m.group(4) else ref.year
            d = date(y, MONTHS[m.group(3).lower()], int(m.group(2)))
            if not m.group(4) and d < ref - timedelta(days=1):
                d = date(y + 1, d.month, d.day)
        except ValueError:
            continue
        if ref <= d <= ref + timedelta(days=90):
            best = d
            break
    if not best:
        m = _WEEKDAY.search(text)
        if m:
            wd = DAYS[m.group(1).lower()]
            delta = (wd - ref.weekday()) % 7 or 7
            best = ref + timedelta(days=delta)
    if not best:
        return None, ''
    t = _TIME.search(text)
    hhmm = ''
    if t and 0 <= int(t.group(1)) <= 23:
        hhmm = '%02d:%s' % (int(t.group(1)), t.group(2) or '00')
    return best, hhmm


def from_candidate(cid):
    """Si la noticia anuncia algo con fecha (pleno, concierto, plazo…), va a la agenda."""
    _ensure()
    c = db.row('SELECT id,title,excerpt,url,published_at,status FROM candidates WHERE id=?', (cid,))
    if not c or c.get('status') in ('archived', 'merged'):
        return None
    text = (c['title'] or '') + '. ' + (c['excerpt'] or '')[:700]
    if not _KIND_RX.search(text):
        return None
    try:
        ref = datetime.fromisoformat(str(c.get('published_at') or '').replace('Z', '+00:00')).astimezone(TZ).date()
    except ValueError:
        ref = None
    today = datetime.now(TZ).date()
    d, hhmm = find_date(text, max(ref or today, today))
    if not d or db.row('SELECT 1 FROM events WHERE candidate_id=?', (cid,)):
        return None
    if db.row('SELECT 1 FROM events WHERE day=? AND active=1 AND lower(title)=lower(?)', (d.isoformat(), c['title'])):
        return None
    return db.exec_('INSERT INTO events(title,day,time,kind,url,candidate_id) VALUES(?,?,?,?,?,?)',
                    (c['title'][:200], d.isoformat(), hhmm, kind_of(text), c.get('url') or '', cid))


def add(title, day, time='', place='', kind='', url=''):
    _ensure()
    datetime.fromisoformat(day)  # valida
    return db.exec_('INSERT INTO events(title,day,time,place,kind,url,manual) VALUES(?,?,?,?,?,?,1)',
                    (title.strip()[:200], day[:10], (time or '')[:5], (place or '')[:120], (kind or '')[:40], (url or '')[:400]))


def remove(eid):
    _ensure()
    db.exec_('UPDATE events SET active=0 WHERE id=?', (eid,))


def upcoming(days=60):
    _ensure()
    today = datetime.now(TZ).date()
    return db.rows('''SELECT * FROM events WHERE active=1 AND day>=? AND day<=? ORDER BY day,COALESCE(NULLIF(time,''),'99')''',
                   (today.isoformat(), (today + timedelta(days=days)).isoformat()))


def remind(days_before=2):
    """Aviso al móvil de lo que llega en los próximos días (una vez por evento)."""
    _ensure()
    today = datetime.now(TZ).date()
    n = 0
    for e in db.rows('SELECT * FROM events WHERE active=1 AND reminded=0 AND day>=? AND day<=?',
                     (today.isoformat(), (today + timedelta(days=days_before)).isoformat())):
        d = date.fromisoformat(e['day'])
        when = 'hoy' if d == today else 'mañana' if d == today + timedelta(days=1) else 'el %s %d' % (
            ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo'][d.weekday()], d.day)
        try:
            from . import alerts
            alerts.notify('agenda', 'Agenda %s: %s' % (when, e['title'][:90]), 'Prepara la previa' + (' · ' + e['time'] if e.get('time') else ''),
                          '/#agenda', key='agenda:%s' % e['id'])
        except Exception as exc:
            db.log('alert_error', str(exc)[:200])
        db.exec_('UPDATE events SET reminded=1 WHERE id=?', (e['id'],))
        n += 1
    return n
