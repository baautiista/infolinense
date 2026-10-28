"""Revisión automática antes de publicar: avisos (no bloquea) sobre nombres de La Línea mal escritos, cifras que no
están en la fuente, palabras repetidas, comillas sin cerrar, «hoy/mañana» que no cuadran con el día de publicación,
días de la semana equivocados y titulares repetidos. Con IA, además, faltas de ortografía."""
import difflib
import re
import unicodedata
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import db

TZ = ZoneInfo('Europe/Madrid')
# Nombres propios de La Línea que se escriben mal a menudo
LOCAL_NAMES = ['La Línea de la Concepción', 'Atunara', 'San Bernardo', 'Santa Margarita', 'Junquillos', 'El Zabal', 'La Colonia',
               'Alcaidesa', 'Poniente', 'Levante', 'Sobrevela', 'El Conchal', 'San Pedro', 'Periáñez', 'Huerta Fava', 'La Velada',
               'Cruz Herrera', 'Juan Franco', 'Balona', 'Real Balompédica Linense', 'Calle Real', 'Avenida de España',
               'Príncipe de Asturias', 'Palacio de Congresos', 'Gibraltar', 'Verja', 'Campo de Gibraltar', 'Algeciras',
               'San Roque', 'Los Barrios', 'Sotogrande', 'Guadiaro', 'Torreguadiaro', 'Santa Bárbara', 'Mancomunidad',
               'Picardo', 'Albares', 'Manolo Alés', 'Paseo Marítimo', 'Ciudad Jardín', 'El Higuerón', 'Las Palomas', 'La Atunara',
               'Mirador', 'Puerta de Tierra', 'Fuerte de Santa Bárbara', 'Teatro Paseo de la Velada', 'Museo Cruz Herrera']
_WD = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']
_MONTHS = {'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
           'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12}


def _fold(text):
    text = unicodedata.normalize('NFD', str(text or '').lower())
    return ''.join(c for c in text if unicodedata.category(c) != 'Mn')


def _names(text):
    out = []
    folded_known = {_fold(n): n for n in LOCAL_NAMES}
    singles = {_fold(w): w for n in LOCAL_NAMES for w in n.split() if len(w) >= 5}
    for w in set(re.findall(r'\b[A-ZÁÉÍÓÚÑ][a-záéíóúñü]{4,}\b', text or '')):
        f = _fold(w)
        if f in singles or f in folded_known:
            if singles.get(f, w) != w and f == _fold(singles.get(f, w)):
                out.append('Tilde: escribe «%s» (pone «%s»)' % (singles[f], w))
            continue
        close = difflib.get_close_matches(f, list(singles), n=1, cutoff=0.86)
        if close:
            out.append('¿«%s»? Pone «%s»' % (singles[close[0]], w))
    return out


def _numbers(text):
    return {re.sub(r'[.\s]', '', n).replace(',', '.') for n in re.findall(r'\b\d[\d.,]*\d\b|\b\d\b', text or '')}


def check(aid, with_ai=False):
    a = db.row('''SELECT a.*,c.raw_text,c.excerpt,c.title src_title,c.planned_at FROM articles a
                  LEFT JOIN candidates c ON c.id=a.candidate_id WHERE a.id=?''', (aid,))
    if not a:
        return []
    head, sub, body = a.get('headline') or '', a.get('subtitle') or '', a.get('body') or ''
    alltext = '\n'.join([head, sub, body])
    source = ' '.join([a.get('raw_text') or '', a.get('excerpt') or '', a.get('src_title') or ''])
    out = []

    def warn(level, field, msg):
        out.append({'level': level, 'field': field, 'message': msg})

    for m in _names(alltext):
        warn('error', 'texto', m)
    if source.strip() and not str(a.get('url') or '').startswith('manual:'):
        src_nums = _numbers(source)
        for field, text in (('titular', head), ('entradilla', sub)):
            for n in _numbers(text) - src_nums:
                if len(n) >= 2 and not re.fullmatch(r'20\d\d', n):
                    warn('warn', field, 'La cifra «%s» no aparece en la fuente: compruébala' % n)
    for m in re.finditer(r'\b(\w{2,})\s+\1\b', alltext, re.I):
        warn('error', 'texto', 'Palabra repetida: «%s %s»' % (m.group(1), m.group(1)))
    if alltext.count('«') != alltext.count('»'):
        warn('warn', 'texto', 'Hay comillas «» sin cerrar')
    if alltext.count('(') != alltext.count(')'):
        warn('warn', 'texto', 'Hay un paréntesis sin cerrar')
    if alltext.count('"') % 2:
        warn('warn', 'texto', 'Hay comillas " sin cerrar')
    if re.search(r' {2,}', alltext):
        warn('info', 'texto', 'Hay espacios dobles')
    if re.search(r'\s[,.;:]', alltext):
        warn('info', 'texto', 'Hay un espacio antes de una coma o un punto')
    if head.rstrip().endswith('.'):
        warn('info', 'titular', 'El titular no lleva punto final')
    if len(head) > 120:
        warn('warn', 'titular', 'Titular muy largo (%d caracteres)' % len(head))
    elif 0 < len(head) < 40:
        warn('info', 'titular', 'Titular corto: añade el dato clave')
    if head and sub and difflib.SequenceMatcher(None, _fold(head), _fold(sub)).ratio() > 0.7:
        warn('warn', 'entradilla', 'La entradilla repite el titular')
    if head == head.upper() and len(head) > 10:
        warn('warn', 'titular', 'El titular está todo en mayúsculas')
    try:
        from . import sources
        if head:
            words = set(w for w in re.sub(r'\W+', ' ', head.lower()).split() if len(w) > 3)
            others = [r['headline'] for r in db.rows("""SELECT headline FROM articles WHERE status='published' AND id!=?
                                                      AND updated_at>=datetime('now','-60 day') ORDER BY id DESC LIMIT 400""", (aid,))]
            for other in others + sources.site_titles():
                ow = set(w for w in re.sub(r'\W+', ' ', (other or '').lower()).split() if len(w) > 3)
                if len(words) >= 3 and ow and (len(words & ow) / max(1, min(len(words), len(ow))) >= 0.7 or sources.same_story(head, other)):
                    warn('error', 'titular', 'Parece repetida con una ya publicada: «%s»' % other[:90])
                    break
    except Exception:
        pass
    # «hoy», «mañana», «ayer» frente al día en que sale
    when = a.get('scheduled_at') or a.get('planned_at') or ''
    try:
        pub_day = datetime.fromisoformat(str(when).replace('Z', '+00:00')).astimezone(TZ).date() if when else datetime.now(TZ).date()
    except ValueError:
        pub_day = datetime.now(TZ).date()
    written = (a.get('time_ref') or str(a.get('updated_at') or '')[:10] or '')[:10]
    if re.search(r'\b(hoy|mañana|ayer|esta\s+mañana|esta\s+tarde|esta\s+noche)\b', alltext, re.I) and written and written != pub_day.isoformat():
        warn('warn', 'texto', 'Dice «hoy/mañana/ayer» y sale otro día (%s): revisa las fechas' % pub_day.strftime('%d/%m'))
    # «el lunes 3 de noviembre»: ¿cuadra el día de la semana?
    for m in re.finditer(r'\b(lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo),?\s+(\d{1,2})\s+de\s+(%s)\b' % '|'.join(_MONTHS), alltext, re.I):
        try:
            y = pub_day.year
            d = date(y, _MONTHS[m.group(3).lower()], int(m.group(2)))
            if (d - pub_day).days < -60:
                d = date(y + 1, d.month, d.day)
            real = _WD[d.weekday()]
            if _fold(real) != _fold(m.group(1)):
                warn('error', 'texto', 'El %s de %s es %s, no %s' % (m.group(2), m.group(3), real, m.group(1)))
        except ValueError:
            warn('error', 'texto', 'Fecha imposible: «%s»' % m.group(0))
    if with_ai:
        out += _ai_check(head, sub, body)
    order = {'error': 0, 'warn': 1, 'info': 2}
    return sorted(out, key=lambda x: order[x['level']])


def _ai_check(head, sub, body):
    from . import ai
    if not ai.AI_ENABLED:
        return [{'level': 'info', 'field': 'IA', 'message': 'La revisión con IA no está disponible (sin IA configurada)'}]
    prompt = ('Revisa SOLO ortografía, gramática y nombres propios de esta noticia de La Línea de la Concepción. No cambies el estilo. '
              'Devuelve JSON {"errors":[{"field":"titular|entradilla|texto","wrong":"texto exacto con el error","right":"corrección"}]} '
              '(lista vacía si no hay errores; máximo 15).\nTITULAR: %s\nENTRADILLA: %s\nTEXTO: %s') % (head, sub, body[:6000])
    try:
        data, _, _ = ai.ask_json(ai.SYSTEM, prompt, max_tokens=1500)
    except Exception as exc:
        return [{'level': 'info', 'field': 'IA', 'message': 'La IA no pudo revisar: %s' % str(exc)[:120]}]
    out = []
    for e in (data or {}).get('errors') or []:
        if isinstance(e, dict) and e.get('wrong') and e.get('right') and e['wrong'] != e['right']:
            out.append({'level': 'error', 'field': str(e.get('field') or 'texto')[:20],
                        'message': '«%s» → «%s»' % (str(e['wrong'])[:80], str(e['right'])[:80])})
    return out or [{'level': 'info', 'field': 'IA', 'message': 'La IA no ha encontrado faltas'}]
