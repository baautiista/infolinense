"""Medios del panel: InfoLinense, El Cofrade Linense y Carnavalinense.

Cada medio tiene sus fuentes, su plantilla de Canva, sus cuentas de redes y su web.
- Los datos no secretos (plantillas, secciones, hashtags) se guardan en la base de datos y se cambian en Ajustes → Medios.
- Lo secreto (web, claves) va en Railway con un prefijo por medio: COFRADE_… y CARNAVAL_… (InfoLinense, sin prefijo).
"""
import json
import os
import re
import unicodedata

from . import db

DEFAULT = 'infolinense'
BRANDS = {
    'infolinense': {'name': 'InfoLinense', 'short': 'InfoLinense', 'color': '#0150FE', 'text': '#FFFFFF', 'env': '',
                    'about': 'medio digital de noticias de La Línea de la Concepción (Cádiz)',
                    'hashtags': '#LaLínea #LaLíneaDeLaConcepción #InfoLinense', 'sections': []},
    'cofrade': {'name': 'El Cofrade Linense', 'short': 'Cofrade', 'color': '#5B2A86', 'text': '#FFFFFF', 'env': 'COFRADE_',
                'about': 'medio cofrade de La Línea de la Concepción: hermandades, cofradías, Semana Santa, glorias y cultos',
                'hashtags': '#ElCofradeLinense #SemanaSantaLaLínea #LaLínea',
                'sections': ['HERMANDADES', 'SEMANA SANTA', 'CULTOS', 'GLORIAS', 'PATRIMONIO', 'AGENDA']},
    'carnaval': {'name': 'Carnavalinense', 'short': 'Carnaval', 'color': '#E72E79', 'text': '#FFFFFF', 'env': 'CARNAVAL_',
                 'about': 'medio del Carnaval de La Línea de la Concepción: agrupaciones, concurso, coplas, cabalgata y fiesta',
                 'hashtags': '#Carnavalinense #CarnavalLaLínea #LaLínea',
                 'sections': ['AGRUPACIONES', 'CONCURSO', 'COPLAS', 'CALLE', 'CABALGATA', 'AGENDA']},
}

_KEYWORDS = {
    'cofrade': r'\b(hermandad(es)?|cofrad[ií]as?|cofrades?|semana\s+santa|cuaresma|costaler[oa]s?|nazarenos?|procesi[oó]n(es)?|'
               r'besamanos|besapi[eé]s|v[ií]a\s*crucis|consejo\s+de\s+hermandades|capataz|imaginer[oíi]a?|saetas?|'
               r'banda\s+de\s+cornetas|paso\s+de\s+(misterio|palio)|palio|triduo|quinario|pregón\s+de\s+(la\s+)?semana\s+santa|'
               r'salida\s+procesional|estaci[oó]n\s+de\s+penitencia|coronaci[oó]n\s+can[oó]nica)\b',
    'carnaval': r'\b(carnaval(es)?|chirigotas?|comparsas?|murgas?|cuartetos?|coros?\s+(de\s+carnaval|del\s+carnaval)|'
                r'agrupaci[oó]n(es)?\s+(carnavalesca|del\s+carnaval)|coac|concurso\s+de\s+agrupaciones|pregón\s+del\s+carnaval|'
                r'cabalgata\s+del\s+carnaval|ilegales?\s+del\s+carnaval|antifaz|carnavaler[oa]s?|cuplés?|pasodobles?|popurr[ií])\b',
}


def valid(slug):
    return slug if slug in BRANDS else DEFAULT


def _plain(text):
    text = unicodedata.normalize('NFC', str(text or '').lower())
    return text


def classify(text):
    """Medio que corresponde por el tema (cofradías o carnaval); None si es noticia general."""
    t = _plain(text)
    hits = {b: len(re.findall(rx, t, re.I)) for b, rx in _KEYWORDS.items()}
    best = max(hits, key=hits.get)
    return best if hits[best] >= 1 else None


# ---------- ajustes (no secretos) ----------

def init_tables():
    db.exec_('CREATE TABLE IF NOT EXISTS brand_settings(slug TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)')


def settings(slug):
    slug = valid(slug)
    base = dict(BRANDS[slug])
    base['slug'] = slug
    base['templates'] = {'main': '', '1': '', '2': '', '4': ''}
    base['page'] = 1
    try:
        row = db.row('SELECT data FROM brand_settings WHERE slug=?', (slug,))
        saved = json.loads(row['data']) if row else {}
    except Exception:
        saved = {}
    for k in ('hashtags', 'sections', 'page', 'about'):
        if saved.get(k) not in (None, '', []):
            base[k] = saved[k]
    base['templates'].update({k: v for k, v in (saved.get('templates') or {}).items() if v})
    return base


def template_id(value):
    """Acepta el ID (EAH…) o el enlace de Canva de la plantilla de marca."""
    value = (value or '').strip()
    m = re.search(r'brand-templates/([A-Za-z0-9_-]{8,})', value) or re.search(r'[?&]template=([A-Za-z0-9_-]{8,})', value) \
        or re.fullmatch(r'([A-Za-z0-9_-]{8,})', value)
    return m.group(1) if m else ''


def save_settings(slug, data):
    slug = valid(slug)
    cur = settings(slug)
    out = {'templates': dict(cur['templates']), 'hashtags': cur['hashtags'], 'sections': cur['sections'],
           'page': cur['page'], 'about': cur['about']}
    for k, v in (data.get('templates') or {}).items():
        if k in ('main', '1', '2', '4'):
            tid = template_id(v)
            if v and not tid:
                raise ValueError('No entiendo la plantilla «%s». Pega el enlace de la plantilla de marca de Canva.' % str(v)[:60])
            out['templates'][k] = tid
    if 'hashtags' in data:
        out['hashtags'] = re.sub(r'\s+', ' ', str(data['hashtags'] or '')).strip()[:300]
    if 'sections' in data:
        secs = data['sections'] if isinstance(data['sections'], list) else str(data['sections'] or '').split(',')
        out['sections'] = [s.strip().upper()[:30] for s in secs if s.strip()][:16]
    if 'page' in data:
        try:
            out['page'] = max(1, min(50, int(data['page'])))
        except (TypeError, ValueError):
            pass
    db.exec_('INSERT OR REPLACE INTO brand_settings(slug,data,updated_at) VALUES(?,?,CURRENT_TIMESTAMP)',
             (slug, json.dumps(out, ensure_ascii=False)))
    return settings(slug)


# ---------- web de cada medio (secretos en Railway) ----------

def env(slug, key, default=''):
    return os.getenv(BRANDS[valid(slug)]['env'] + key, default).strip()


def web_config(slug):
    slug = valid(slug)
    if slug == DEFAULT:
        from . import publishers as c  # (lee los ajustes de Railway a través de publishers)
        return {'mode': c.PUBLISH_MODE if c.AUTO_PUBLISH else 'none', 'webhook_url': c.LOVABLE_WEBHOOK_URL,
                'secret': c.PUBLISH_WEBHOOK_SECRET or c.LOVABLE_WEBHOOK_TOKEN, 'wp_url': c.WORDPRESS_URL,
                'wp_user': c.WORDPRESS_USERNAME, 'wp_password': c.WORDPRESS_APP_PASSWORD}
    mode = env(slug, 'PUBLISH_MODE').lower() or ('webhook' if env(slug, 'WEBHOOK_URL') else 'wordpress' if env(slug, 'WORDPRESS_URL') else 'none')
    return {'mode': mode, 'webhook_url': env(slug, 'WEBHOOK_URL'), 'secret': env(slug, 'WEBHOOK_SECRET'),
            'wp_url': env(slug, 'WORDPRESS_URL').rstrip('/'), 'wp_user': env(slug, 'WORDPRESS_USERNAME'),
            'wp_password': env(slug, 'WORDPRESS_APP_PASSWORD')}


def web_enabled(slug):
    return web_config(slug)['mode'] not in ('', 'none')


def public(slug):
    """Lo que ve el panel (sin secretos)."""
    s = settings(slug)
    w = web_config(slug)
    return {'slug': s['slug'], 'name': s['name'], 'short': s['short'], 'color': s['color'], 'text': s['text'],
            'hashtags': s['hashtags'], 'sections': s['sections'], 'templates': s['templates'], 'page': s['page'],
            'web': {'enabled': web_enabled(slug), 'mode': w['mode'],
                    'host': re.sub(r'^https?://([^/]+).*$', r'\1', w['webhook_url'] or w['wp_url'] or '')},
            'env_prefix': BRANDS[s['slug']]['env']}


def all_public():
    return [public(b) for b in BRANDS]
