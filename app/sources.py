"""Source discovery: factual links for editorial review."""
import difflib
import html
import re
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

from . import db
from .config import MAX_CANDIDATE_AGE_DAYS

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
_scan_lock = threading.Lock()
# La Línea de la Concepción (la ciudad), no «la línea 1 del metro», «línea de alta velocidad», etc.
_CITY = re.compile(r'(La\s+L[ií]nea\s+de\s+la\s+Concepci[oó]n|\blinens[ea]s?\b|\bLa\s+L[ií]nea\b)', re.I)
_CITY_STRONG = re.compile(r'(l[ií]nea\s+de\s+la\s+concepci[oó]n|\blinens[ea]s?\b|\batunara\b|real\s+balompédica|\bbalona\b|'
                          r'san\s+bernardo|santa\s+margarita|\bel\s+zabal\b|la\s+colonia|junquillos|campo\s+de\s+gibraltar)', re.I)
_CONTEXT = re.compile(r'(gibraltar|c[aá]diz|algeciras|san\s+roque|frontera|verja|ayuntamiento|juan\s+franco|poniente|levante|'
                      r'alcaidesa|cruz\s+herrera|parque\s+princesa\s+sof[ií]a|feria|velada|campo\s+de\s+gibraltar)', re.I)
_UNRELATED = re.compile(r'\bl[ií]neas?\s+(?:\d+|[a-z]\d*\b|de\s+metro|del\s+metro|ferroviaria|de\s+tren|de\s+alta|el[eé]ctrica|a[eé]rea|editorial|'
                        r'de\s+autob[uú]s|de\s+salida|de\s+meta|de\s+cr[eé]dito|roja|telef[oó]nica|de\s+fuego|de\s+defensa|de\s+ayuda|'
                        r'de\s+flotaci[oó]n|del\s+horizonte|de\s+producci[oó]n|de\s+negocio|de\s+investigaci[oó]n|de\s+trabajo|'
                        r'argumental|sucesoria|de\s+banda|de\s+cercan[ií]as|de\s+costa|blanca|caliente|directa|de\s+fondo)', re.I)
_LOCAL = _CITY

_MONTHS = {'ene': 1, 'feb': 2, 'mar': 3, 'abr': 4, 'may': 5, 'jun': 6, 'jul': 7, 'ago': 8, 'sep': 9, 'set': 9, 'oct': 10, 'nov': 11, 'dic': 12}


def parse_es_date(text, now=None):
    """Fecha en un texto en español: 05/10/2026, 5 oct 2026, 5 de octubre de 2026, «hace 3 horas», «ayer». ISO o ''."""
    now = now or datetime.now(timezone.utc)
    t = (text or '').lower()
    m = re.search(r'\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b', t)
    if m:
        try: return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), 9, tzinfo=timezone.utc).isoformat()
        except ValueError: pass
    m = re.search(r'\b(\d{1,2})\s+(?:de\s+)?(ene|feb|mar|abr|may|jun|jul|ago|sep|set|oct|nov|dic)[a-z]*\.?(?:\s+(?:de\s+)?(\d{4}))?', t)
    if m:
        year = int(m.group(3) or now.year)
        try:
            d = datetime(year, _MONTHS[m.group(2)], int(m.group(1)), 9, tzinfo=timezone.utc)
            if not m.group(3) and d > now + timedelta(days=1): d = d.replace(year=year - 1)
            return d.isoformat()
        except ValueError: pass
    m = re.search(r'hace\s+(\d+)\s*(min|minuto|h\b|hora|d\b|d[ií]a|sem)', t)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = timedelta(minutes=n) if unit.startswith('min') else timedelta(hours=n) if unit.startswith('h') else timedelta(weeks=n) if unit.startswith('sem') else timedelta(days=n)
        return (now - delta).isoformat(timespec='minutes')
    if re.search(r'\bayer\b', t): return (now - timedelta(days=1)).isoformat(timespec='minutes')
    return ''


def clean(value):
    value = html.unescape(value or '')
    if '<' in value: value = BeautifulSoup(value, 'html.parser').get_text(' ', strip=True)
    return re.sub(r'\s+', ' ', value).strip()

def fetch(url, timeout=18):
    return requests.get(url, timeout=(12, timeout), headers={
        'User-Agent': UA, 'Accept-Language': 'es-ES,es;q=0.9',
        'Accept': 'application/rss+xml,application/atom+xml,application/xml,text/html,*/*;q=0.5',
    })

def best_image(html_text, base=''):
    """La imagen más grande de un fragmento HTML (srcset de WordPress incluido)."""
    if not html_text or '<img' not in html_text:
        return ''
    soup = BeautifulSoup(html_text, 'html.parser')
    for img in soup.find_all('img'):
        options = []
        for part in (img.get('srcset') or '').split(','):
            bits = part.strip().split()
            if bits:
                try: options.append((int(bits[1].rstrip('w')) if len(bits) > 1 and bits[1].endswith('w') else 0, bits[0]))
                except ValueError: options.append((0, bits[0]))
        src = img.get('data-src') or img.get('src') or ''
        if src: options.append((int(img.get('width') or 0) if str(img.get('width') or '').isdigit() else 0, src))
        options = [o for o in options if o[1] and not o[1].startswith('data:')]
        if options:
            url = urljoin(base, max(options)[1])
            if not re.search(r'(logo|icon|avatar|emoji|gravatar)', url, re.I):
                return url
    return ''


def parse_rss(source):
    r = fetch(source['url']); r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for it in root.findall('.//item')[:120]:
        title, link = clean(it.findtext('title')), clean(it.findtext('link'))
        outlet = clean(it.findtext('source'))  # Google News indica el medio original
        if outlet and title.endswith(' - ' + outlet):
            title = title[:-(len(outlet) + 3)].strip()
        if title and link:
            raw = (it.findtext('{http://purl.org/rss/1.0/modules/content/}encoded') or '') + (it.findtext('description') or '')
            media = next((m for m in (it.find('{http://search.yahoo.com/mrss/}content'), it.find('{http://search.yahoo.com/mrss/}thumbnail'),
                                       it.find('enclosure')) if m is not None), None)
            image = media.attrib.get('url', '') if media is not None and 'image' in media.attrib.get('type', 'image') else ''
            full = clean(it.findtext('{http://purl.org/rss/1.0/modules/content/}encoded') or '')
            items.append({'title': title, 'url': link, 'excerpt': full if len(full) > len(clean(it.findtext('description'))) else clean(it.findtext('description')),
                          'published_at': clean(it.findtext('pubDate')) or clean(it.findtext('{http://purl.org/dc/elements/1.1/}date')),
                          'outlet': outlet, 'image': image or best_image(raw, link)})
    if not items:
        ns = {'a': 'http://www.w3.org/2005/Atom'}
        for it in root.findall('.//a:entry', ns)[:120]:
            link = next((x.attrib.get('href', '') for x in it.findall('a:link', ns)
                         if x.attrib.get('rel', 'alternate') == 'alternate'), '')
            title = clean(it.findtext('a:title', default='', namespaces=ns))
            if title and link:
                items.append({'title': title, 'url': link,
                              'excerpt': clean(it.findtext('a:summary', default='', namespaces=ns)
                                               or it.findtext('a:content', default='', namespaces=ns)),
                              'published_at': clean(it.findtext('a:published', default='', namespaces=ns)
                                                    or it.findtext('a:updated', default='', namespaces=ns))})
    return items

def parse_html(source):
    r = fetch(source['url']); r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    for node in soup.select('nav, footer, header, aside, script, style'): node.decompose()
    links = soup.select('article a[href], h2 a[href], h3 a[href]')
    if not links: links = soup.select('main a[href]')
    items, seen = [], set()
    host = urlparse(source['url']).hostname
    for a in links:
        title = clean(a.get_text(' ', strip=True))
        if len(title) < 28 or len(title) > 300: continue
        link = urljoin(source['url'], a.get('href', ''))
        if urlparse(link).scheme not in ('http', 'https') or urlparse(link).hostname != host: continue
        link, _ = urldefrag(link)
        if link in seen or link.rstrip('/') == source['url'].rstrip('/'): continue
        seen.add(link)
        parent = a.find_parent(['article', 'h2', 'h3']) or a.parent
        excerpt = clean(parent.get_text(' ', strip=True) if parent else title)[:900]
        items.append({'title': title, 'url': link, 'excerpt': excerpt, 'published_at': ''})
        if len(items) >= 100: break
    return items

def parse_bop(source):
    """Read individual announcements from the latest two provincial bulletins."""
    r = fetch(source['url']); r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    bulletins = []
    for a in soup.select('a[href]'):
        link = urljoin(source['url'], a.get('href', ''))
        if '/boletin/Boletin-numero-' in link and link not in bulletins: bulletins.append(link)
        if len(bulletins) == 2: break
    if not bulletins: raise ValueError('No se encontraron boletines recientes')
    results = []
    for link in bulletins:
        page = fetch(link); page.raise_for_status()
        bsoup = BeautifulSoup(page.text, 'html.parser')
        head = ' '.join(x.get_text(' ', strip=True) for x in bsoup.select('title, h1, h2')[:4])
        bulletin_date = parse_es_date(head) or parse_es_date(bsoup.get_text(' ', strip=True)[:1500])
        for a in bsoup.select('a[href]'):
            title = clean(a.get_text(' ', strip=True))
            match = re.match(r'^(\d{2,3}\.\d{3})\s*\.?\s*-', title)
            if not match or not exact_locality(title): continue
            doc = urljoin(link, a.get('href', ''))
            base, fragment = urldefrag(doc)
            permalink = base + '#' + (fragment + '&' if fragment else '') + 'anuncio=' + match.group(1).replace('.', '')
            results.append({'title': title[:260], 'url': permalink,
                            'excerpt': title, 'published_at': bulletin_date})
    return results

def parse_procurement(source):
    """Licitaciones del Ayuntamiento en Gobierto: cada enlace /licitaciones/N con su fecha, importe y cierre."""
    r = fetch(source['url'], timeout=30); r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    results, seen = [], set()
    now = datetime.now(timezone.utc)
    for a in soup.find_all('a', href=re.compile(r'/licitaciones/\d+')):
        url = urljoin(source['url'], a['href'].split('?')[0])
        title = clean(a.get_text(' ', strip=True))
        if url in seen or len(title) < 12: continue
        row = a.find_parent('tr') or a.find_parent(['li', 'article']) or a.parent
        text = clean(row.get_text(' ', strip=True)) if row else title
        published = ''
        cell = row.select_one('td[data-toggle-column-id="call_for_tenders_published_at"]') if row else None
        if cell is not None and str(cell.get('data-sort-value', '')).isdigit():
            published = datetime.fromtimestamp(int(cell['data-sort-value']), timezone.utc).isoformat()
        dates = [publication_datetime(parse_es_date(m.group(0))) for m in re.finditer(
            r'\b\d{1,2}[/.-]\d{1,2}[/.-]\d{4}\b|\b\d{1,2}\s+(?:de\s+)?(?:ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)[a-z]*\.?\s+(?:de\s+)?\d{4}', text.lower())]
        dates = [d for d in dates if d]
        if not published and dates:
            published = min(dates).isoformat()  # la fecha más antigua de la fila es la de publicación
        closing = max(dates) if len(dates) > 1 else None
        if not published: continue  # sin fecha no se puede asegurar que sea actual
        still_open = closing is not None and closing >= now
        if not still_open and not recent_enough(published, WINDOW_DAYS['Licitaciones y edictos']): continue
        amount = re.search(r'\d[\d.,]*\s*(?:M\s*)?€', text)
        excerpt = ' · '.join(x for x in [('Importe: ' + amount.group(0)) if amount else '',
                                         ('Plazo hasta ' + closing.strftime('%d/%m/%Y')) if closing else ''] if x)
        seen.add(url)
        results.append({'title': 'Licitación: ' + title[:240], 'url': url, 'excerpt': excerpt or text[:400], 'published_at': published})
    if not results and 'licitaciones/' not in r.text:
        raise ValueError('La página de licitaciones no mostró ninguna licitación (puede haber cambiado de formato)')
    return results

def _dmy(value):
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', value or '')
    if not m: return ''
    return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), 9, 0, tzinfo=timezone.utc).isoformat()


_EXPIRED = re.compile(r'sesi[oó]n ha expirado|vuelva a iniciar sesi[oó]n|espere un momento por favor', re.I)


def fetch_edictos_html(url):
    """La sede pide una sesión (cookie): se abre primero el tablón, se siguen sus redirecciones y luego se lee el listado."""
    s = requests.Session()
    s.headers.update({'User-Agent': UA, 'Accept-Language': 'es-ES,es;q=0.9',
                      'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'})
    parsed = urlparse(url)
    root = '%s://%s/' % (parsed.scheme, parsed.netloc)
    base = url.split('/edicto/')[0].rstrip('/') + '/' if '/edicto/' in url else root
    last = None
    for attempt in range(3):
        try:
            last = s.get(url, timeout=30, headers={'Referer': base})
            if last.ok and not _EXPIRED.search(last.text[:20000]):
                return last.text
        except requests.RequestException:
            if attempt == 2: raise
        for warm in (root, base):  # conseguir la cookie de sesión como haría un navegador
            try:
                w = s.get(warm, timeout=30)
                m = re.search(r"""(?:location(?:\.href)?\s*=\s*|location\.replace\(\s*|http-equiv=["']?refresh["']?[^>]*url=)["']?([^"'\s;>)]+)""", w.text, re.I)
                if m:
                    s.get(urljoin(w.url, html.unescape(m.group(1))), timeout=30, headers={'Referer': warm})
            except requests.RequestException:
                pass
        time.sleep(1.5 * (attempt + 1))
    if last is not None and _EXPIRED.search(last.text[:20000]):
        raise ValueError('La sede electrónica no dejó leer el tablón (pide sesión). Se vuelve a intentar en el próximo escaneo; '
                         'mientras tanto entran los edictos indexados por Google.')
    if last is not None: last.raise_for_status()
    return last.text if last is not None else ''


def parse_edictos(source):
    """Tablón de edictos de la sede electrónica: tabla con fecha de publicación, fin, título y enlace."""
    page = fetch_edictos_html(source['url'])
    soup = BeautifulSoup(page, 'html.parser')
    out, seen = [], set()

    def add(title, href, published):
        href = re.sub(r';jsessionid=[^?#]+', '', href, flags=re.I)
        url = urljoin(source['url'], href)
        if not title or not published or url in seen: return
        if not recent_enough(published, WINDOW_DAYS['Licitaciones y edictos']): return
        seen.add(url)
        out.append({'title': 'Edicto: ' + title[:240], 'url': url, 'excerpt': title, 'published_at': published})

    for row in soup.select('tr'):
        cells = row.find_all('td')
        if len(cells) < 3: continue
        link = row.find('a', href=re.compile(r'codigo=|edicto', re.I))
        if not link: continue
        title = ''
        for c in cells:
            text = clean(c.get_text(' ', strip=True))
            if len(text) > len(title) and not re.fullmatch(r'[\d/ :.-]+', text): title = text
        add(title, link['href'], _dmy(row.get_text(' ', strip=True)))  # la primera fecha de la fila es la de publicación
    if not out:  # formato sin tabla: bloques con enlace y fecha
        for link in soup.find_all('a', href=re.compile(r'codigo=|ver-?edicto|detalle', re.I)):
            block = link
            for _ in range(4):
                if block.parent is None or _dmy(block.get_text(' ', strip=True)): break
                block = block.parent
            title = clean(link.get_text(' ', strip=True))
            if len(title) < 15:
                title = clean(re.sub(r'\d{1,2}/\d{1,2}/\d{4}(\s+\d{1,2}:\d{2}(:\d{2})?)?', ' ', block.get_text(' ', strip=True)))
            add(title, link['href'], _dmy(block.get_text(' ', strip=True)))
    return out


def exact_locality(text):
    """Solo la ciudad de La Línea de la Concepción."""
    text = text or ''
    if _CITY_STRONG.search(text):
        return True
    # «La Línea» a secas: con mayúscula y con contexto local, y sin ser una línea de metro, tren, etc.
    if not re.search(r'\bLa\s+L[ií]nea\b', text):
        return False
    if _UNRELATED.search(text):
        return False
    return bool(_CONTEXT.search(text))

def publication_datetime(value):
    if not value: return None
    try: date = parsedate_to_datetime(value)
    except (ValueError, TypeError, IndexError):
        try: date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError: return None
    if not date.tzinfo: date = date.replace(tzinfo=timezone.utc)
    return date.astimezone(timezone.utc)

# Días que una pieza sigue siendo actual según su bloque
WINDOW_DAYS = {'Licitaciones y edictos': 20, 'Ayuntamiento': 3}


def recent_enough(value, days=None):
    date = publication_datetime(value)
    if date is None: return True  # HTML listings often omit dates; URL dedup handles repeats.
    return date >= datetime.now(timezone.utc) - timedelta(days=days or MAX_CANDIDATE_AGE_DAYS)

def heuristic_score(title, excerpt, source):
    text = (title + ' ' + (excerpt or '')).lower()
    score = 28 if exact_locality(text) or source.get('local_scope') else 0
    if source.get('official'): score += 14
    score += round(int(source.get('priority') or 50) * 0.10)
    for kw in ['obra', 'urbanismo', 'licit', 'contrato', 'presupuesto', 'adjudic', 'vivienda',
               'frontera', 'gibraltar', 'empleo', 'apertura', 'comercio', 'agenda', 'cultura',
               'patrimonio', 'servicio', 'tráfico', 'movilidad', 'subvención', 'ayuda', 'playa',
               'parque', 'puerto', 'hospital']:
        if kw in text: score += 4
    if re.search(r'\b\d[\d\.,]*\b', text): score += 2
    return max(0, min(100, score))

def is_duplicate(title, url):
    if url and (db.row('SELECT id FROM candidates WHERE url=?', (url,)) or db.row('SELECT id FROM candidate_links WHERE url=?', (url,))): return True
    if _TENDER.search(title or ''): return False  # edictos y licitaciones: cada documento es distinto aunque se parezcan
    normalized = re.sub(r'\W+', ' ', re.sub(r'\s+[-–|]\s+[^-–|]{3,35}$', '', title).lower()).strip()
    if len(normalized) < 28: return False
    for item in db.rows('SELECT title FROM candidates ORDER BY id DESC LIMIT 300'):
        prior = re.sub(r'\W+', ' ', re.sub(r'\s+[-–|]\s+[^-–|]{3,35}$', '', item['title']).lower()).strip()
        if difflib.SequenceMatcher(None, normalized, prior).ratio() > 0.90: return True
    return False

_DATE_META = ('article:published_time', 'og:published_time', 'datePublished', 'pubdate', 'publish-date',
              'DC.date.issued', 'dc.date', 'sailthru.date', 'parsely-pub-date')


def page_date(url):
    """Fecha de publicación leída de la propia página (meta, <time> o JSON-LD)."""
    try:
        r = fetch(url, timeout=12)
        if not r.ok: return ''
        soup = BeautifulSoup(r.text[:400000], 'html.parser')
        for key in _DATE_META:
            tag = soup.find('meta', attrs={'property': key}) or soup.find('meta', attrs={'name': key}) or soup.find('meta', attrs={'itemprop': key})
            if tag and tag.get('content') and publication_datetime(tag['content'].strip()):
                return tag['content'].strip()
        m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', r.text)
        if m and publication_datetime(m.group(1)): return m.group(1)
        art = soup.find('article')
        t = art.find('time', attrs={'datetime': True}) if art else None  # solo dentro de la noticia, no la fecha de la cabecera
        if t and publication_datetime(t['datetime']): return t['datetime']
    except Exception:
        pass
    return ''


BLOCKS = ['Ayuntamiento', 'Licitaciones y edictos', 'Otros medios', 'Nacionales adaptables']


def source_group(row):
    """Bloque del radar: Ayuntamiento, Licitaciones y edictos, Otros medios, Nacionales adaptables (o Redes sociales)."""
    kind = (row.get('source_kind') or row.get('kind') or '').lower()
    name = (row.get('source_name') or '').lower()
    blob = ' '.join(str(row.get(k) or '') for k in ('source_name', 'outlet', 'url', 'title')).lower()
    if kind == 'social' or row.get('social_type') or 'facebook.com' in blob or 'instagram.com' in blob:
        return 'Redes sociales'
    if kind in ('procurement', 'bop', 'edictos') or any(w in blob for w in ('licitac', 'contratac', 'contratos.gobierto', 'edicto', 'bopcadiz',
                                                                         'boe.es', 'boja', 'adjudica', 'sedeelectronica')):
        return 'Licitaciones y edictos'
    if 'lalinea.es' in blob or name.startswith('ayuntamiento'):
        return 'Ayuntamiento'
    if (row.get('scope') or '') in ('nacional', 'internacional', 'regional') or 'nacional' in name:
        return 'Nacionales adaptables'
    return 'Otros medios'


def similar_to_published(title):
    """¿Ya hemos publicado (o aprobado) algo casi igual?"""
    norm = lambda t: re.sub(r'\W+', ' ', (t or '').lower()).strip()
    words = set(w for w in norm(title).split() if len(w) > 3)
    if len(words) < 3: return False
    for a in db.rows("SELECT headline FROM articles WHERE status IN ('approved','published') ORDER BY id DESC LIMIT 300"):
        other = set(w for w in norm(a['headline']).split() if len(w) > 3)
        if other and len(words & other) / max(1, min(len(words), len(other))) >= 0.7:
            return True
    return False


_STORY_STOP = set('''para como pero desde hasta sobre entre tras ante bajo contra durante mediante segun este esta estos estas
ese esos esas aquel todo toda todos todas otro otra otros otras nuestro nueva nuevo nuevas nuevos donde cuando quien quienes cual
cuales tambien muy mas menos han hay ser sido sera fue son estan tiene tienen hace hacen linea concepcion linense linenses
ayuntamiento municipal ciudad vecinos edicto licitacion noticia hoy ayer manana tras'''.split())
_TENDER = re.compile(r'licitaci|adjudica|edicto|contrataci', re.I)


def _story_tokens(title):
    t = re.sub(r'\s+[-–|]\s+[^-–|]{3,40}$', '', title or '')  # quita « - Europa Sur»
    t = unicodedata.normalize('NFD', t.lower())
    t = ''.join(c for c in t if unicodedata.category(c) != 'Mn')
    out = set()
    for w in re.findall(r'[a-z0-9ñ]+', t):
        if w.isdigit():
            if not re.fullmatch(r'20[12]\d', w): out.add(w)  # las cifras distinguen noticias; los años no
        elif len(w) >= 4 and w not in _STORY_STOP:
            out.add(w[:6])
    return out


def same_story(a, b):
    """¿Dos titulares cuentan la misma noticia? (palabras clave en común, sin contar «La Línea», años, etc.)"""
    ta, tb = _story_tokens(a), _story_tokens(b)
    if min(len(ta), len(tb)) < 3: return False
    shared = len(ta & tb)
    if shared >= 3 and shared / min(len(ta), len(tb)) >= 0.6:
        return True
    norm = lambda t: ' '.join(sorted(_story_tokens(t)))
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio() >= 0.85


def find_story(title, exclude=None, days=4):
    """Noticia abierta del radar (últimos días) que cuenta lo mismo."""
    if _TENDER.search(title or ''): return None  # cada edicto o licitación es un documento distinto
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    for c in db.rows("""SELECT id,title FROM candidates WHERE status NOT IN ('archived','rejected','merged')
                        AND created_at>=? ORDER BY id""", (since,)):
        if c['id'] != exclude and not _TENDER.search(c['title'] or '') and same_story(title, c['title']):
            return c['id']
    return None


def attach_link(cid, title, url, excerpt='', source_name='', outlet='', published_at=''):
    """Añade otra fuente a una noticia ya existente."""
    db.exec_('INSERT OR IGNORE INTO candidate_links(candidate_id,source_name,outlet,url,title,excerpt,published_at) VALUES(?,?,?,?,?,?,?)',
             (cid, source_name, clean(outlet)[:120], url, clean(title)[:260], clean(excerpt)[:4000], published_at or ''))
    n = db.row('SELECT COUNT(*) n FROM candidate_links WHERE candidate_id=?', (cid,))['n']
    # varias fuentes cuentan lo mismo: es más relevante
    db.exec_("""UPDATE candidates SET score=MIN(100,score+3),
                relevance=CASE WHEN MIN(100,score+3)>=70 THEN 'high' WHEN MIN(100,score+3)>=50 THEN 'medium' ELSE relevance END
                WHERE id=? AND ?<=5""", (cid, n))


def links_for(ids):
    if not ids: return {}
    out = {}
    q = 'SELECT * FROM candidate_links WHERE candidate_id IN (%s) ORDER BY id' % ','.join('?' * len(ids))
    for r in db.rows(q, tuple(ids)):
        out.setdefault(r['candidate_id'], []).append({'source_name': r['source_name'], 'outlet': r['outlet'], 'url': r['url'],
                                                      'title': r['title'], 'published_at': r['published_at']})
    return out


def merge_duplicates(days=7):
    """Une en una sola tarjeta las noticias del radar que son la misma historia contada por distintas fuentes."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    rows = db.rows("""SELECT c.*,(SELECT id FROM articles a WHERE a.candidate_id=c.id AND a.status!='rejected') article_id
                      FROM candidates c WHERE c.status NOT IN ('archived','rejected','merged') AND c.created_at>=? ORDER BY c.id""", (since,))
    rows = [r for r in rows if not _TENDER.search(r['title'] or '') and r.get('social_type') in (None, '')]
    rank = lambda r: (1 if r.get('article_id') else 0, 1 if (r.get('editorial_priority') or 'undecided') not in ('undecided', '') else 0, -r['id'])
    gone, merged = set(), 0
    for i, a in enumerate(rows):
        if a['id'] in gone: continue
        for b in rows[i + 1:]:
            if b['id'] in gone or not same_story(a['title'], b['title']): continue
            keep, drop = (a, b) if rank(a) >= rank(b) else (b, a)
            if drop.get('article_id') or drop['status'] not in ('new', 'needs_config', 'researched'):
                continue  # algo ya en redacción no se toca
            attach_link(keep['id'], drop['title'], drop['url'], drop.get('excerpt') or '', drop.get('source_name') or '',
                        drop.get('outlet') or '', drop.get('published_at') or '')
            db.exec_('UPDATE candidate_links SET candidate_id=? WHERE candidate_id=?', (keep['id'], drop['id']))
            db.exec_("UPDATE candidates SET status='merged',merged_into=? WHERE id=?", (keep['id'], drop['id']))
            gone.add(drop['id']); merged += 1
            if drop is a: break
    return merged


def add_candidate(title, url, excerpt, source_name, source_id=None, published_at='', source_meta=None, local_angle='', outlet='', image=''):
    source_meta = source_meta or {'priority': 60, 'official': 0, 'local_scope': 0}
    title, excerpt = clean(title)[:260], clean(excerpt)[:4000]
    local_angle = clean(local_angle)[:800]
    tender = source_meta.get('kind') in ('procurement', 'bop', 'edictos') or bool(re.search(r'licitaci|adjudica|edicto|contrataci', title, re.I))
    window = WINDOW_DAYS['Licitaciones y edictos'] if tender else None
    if not title or not url or not recent_enough(published_at, window): return None
    if not exact_locality(title + ' ' + excerpt + ' ' + local_angle) and not source_meta.get('local_scope'): return None
    if url and (db.row('SELECT 1 FROM candidates WHERE url=?', (url,)) or db.row('SELECT 1 FROM candidate_links WHERE url=?', (url,))):
        return None
    if similar_to_published(title): return None
    same = None if tender else find_story(title)
    if same:  # la misma noticia desde otra fuente: se une a la que ya hay
        attach_link(same, title, url, excerpt, source_name, outlet, published_at)
        return None
    if is_duplicate(title, url): return None
    score = heuristic_score(title, excerpt + ' ' + local_angle, source_meta)
    if tender:
        score = min(100, score + 30)  # licitaciones y edictos, prioridad alta
    relevance = 'low' if score < 50 else ('medium' if score < 70 else 'high')
    return db.exec_('''INSERT OR IGNORE INTO candidates(source_id,source_name,title,url,published_at,excerpt,score,relevance,status,local_angle,outlet,image_hint)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (source_id, source_name, title, url, published_at, excerpt, score, relevance, 'new', local_angle, clean(outlet)[:120], image or ''))

def read_source(source):
    if source['kind'] == 'rss': return parse_rss(source)
    if source['kind'] == 'bop': return parse_bop(source)
    if source['kind'] == 'procurement': return parse_procurement(source)
    if source['kind'] == 'edictos': return parse_edictos(source)
    if source['kind'] == 'html': return parse_html(source)
    if source['kind'] == 'social':
        target = urlparse(source['url'])
        host = (target.hostname or '').lower()
        if host not in ('facebook.com', 'www.facebook.com', 'instagram.com', 'www.instagram.com'):
            raise ValueError('Indica una página pública de Facebook o Instagram')
        parts = (target.path or '/').strip('/').split('/')
        account = '/'.join(parts[:2]) if parts[0] == 'groups' else parts[0]
        if not account: raise ValueError('Indica el nombre de la página o grupo')
        query = f'site:{host}/{account} "La Línea de la Concepción" when:7d'
        feed = 'https://news.google.com/rss/search?q=' + quote(query) + '&hl=es&gl=ES&ceid=ES:es'
        return parse_rss({'url': feed})  # Coverage depends on public search indexing.
    raise ValueError('Tipo de fuente no soportado')

def source_health(sid, seen=0, added=0, error=''):
    db.exec_('''UPDATE sources SET last_checked_at=CURRENT_TIMESTAMP,
               last_success_at=CASE WHEN ?='' THEN CURRENT_TIMESTAMP ELSE last_success_at END,
               last_error=?,items_seen=?,items_added=? WHERE id=?''',
             (error, error[:500], seen, added, sid))

def probe_source(sid):
    source = db.row('SELECT * FROM sources WHERE id=?', (sid,))
    if not source: raise ValueError('Fuente no encontrada')
    try:
        items = read_source(source); source_health(sid, len(items))
        return {'ok': True, 'items_seen': len(items)}
    except Exception as exc:
        source_health(sid, error=str(exc))
        return {'ok': False, 'items_seen': 0, 'error': str(exc)[:250]}

def scan_all():
    if not _scan_lock.acquire(blocking=False):
        return {'added': [], 'errors': [], 'busy': True}
    try:
        added, errors = [], []
        active = db.rows('SELECT * FROM sources WHERE active=1 ORDER BY priority DESC')
        # Fetch concurrently; commit all SQLite writes in this thread.
        with ThreadPoolExecutor(max_workers=6) as pool:
            jobs = {pool.submit(read_source, source): source for source in active}
            for job in as_completed(jobs):
                source = jobs[job]
                try:
                    items = job.result(); count = 0
                    items = [i for i in items if not db.row('SELECT 1 FROM candidates WHERE url=?', (i['url'],))
                             and not db.row('SELECT 1 FROM candidate_links WHERE url=?', (i['url'],))]
                    # Solo noticias actuales: si la lista no trae fecha, se lee de la propia página.
                    undated = [i for i in items if not i.get('published_at') and source['kind'] not in ('procurement', 'edictos')][:40]
                    if undated:
                        with ThreadPoolExecutor(max_workers=8) as dates:
                            for item, date in zip(undated, dates.map(lambda i: page_date(i['url']), undated)):
                                item['published_at'] = date
                    for item in items:
                        if not publication_datetime(item.get('published_at')):
                            continue  # sin fecha comprobable no entra: así nunca se muestra una fecha inventada
                        if publication_datetime(item['published_at']) > datetime.now(timezone.utc) + timedelta(hours=6):
                            continue  # fecha futura: dato erróneo de la fuente
                        cid = add_candidate(item['title'], item['url'], item.get('excerpt', ''),
                                            source['name'], source['id'], item.get('published_at', ''), source,
                                            outlet=item.get('outlet', ''), image=item.get('image', ''))
                        if cid: added.append(cid); count += 1
                    source_health(source['id'], len(items), count)
                except Exception as exc:
                    errors.append(f"{source['name']}: {str(exc)[:250]}")
                    source_health(source['id'], error=str(exc))
        from . import ai
        if any(ai.web_enabled(p) for p in ai.provider_chain()):
            try:
                for item in ai.discover_candidates():
                    meta = {'priority': 85 if item.get('official') else 65,
                            'official': bool(item.get('official')), 'local_scope': False}
                    date = str(item.get('published_at') or '') or page_date(item.get('url', ''))
                    if not date or db.row('SELECT 1 FROM candidates WHERE url=?', (item.get('url', ''),)):
                        continue
                    cid = add_candidate(item.get('title', ''), item.get('url', ''),
                                        item.get('excerpt', ''), 'Búsqueda web',
                                        None, date, meta, item.get('local_angle', ''),
                                        outlet=item.get('source_name', ''))
                    if cid:
                        added.append(cid)
                        scope = str(item.get('scope') or '').lower()
                        db.exec_('UPDATE candidates SET scope=? WHERE id=?', (scope[:20], cid))
            except Exception as exc: errors.append('Descubrimiento web IA: ' + str(exc)[:250])
        merged = merge_duplicates()
        added = [a for a in added if (db.row('SELECT status FROM candidates WHERE id=?', (a,)) or {}).get('status') != 'merged']
        archived = archive_stale()
        db.log('scan', f'Escaneo: {len(added)} nuevas; {merged} unidas por repetidas; {archived} antiguas retiradas; {len(errors)} errores')
        return {'added': added, 'errors': errors, 'sources_checked': len(active), 'busy': False}
    finally:
        _scan_lock.release()

def archive_stale():
    """Retira del radar lo que ya no es actual y nadie ha marcado como útil."""
    now = datetime.now(timezone.utc)
    n = 0
    for row in db.rows("""SELECT c.id,c.published_at,c.created_at,c.source_name,c.outlet,c.url,c.title,c.scope,c.social_type,s.kind
                          FROM candidates c LEFT JOIN sources s ON s.id=c.source_id
                          WHERE c.status IN ('new','researched','needs_config') AND c.editorial_priority IN ('undecided','')"""):
        limit = now - timedelta(days=WINDOW_DAYS.get(source_group(row), MAX_CANDIDATE_AGE_DAYS))
        date = publication_datetime(row.get('published_at')) or publication_datetime((row.get('created_at') or '').replace(' ', 'T') + '+00:00')
        if date and date < limit:
            db.exec_("UPDATE candidates SET status='archived' WHERE id=?", (row['id'],)); n += 1
    return n


_BOILER = re.compile(r'(cookies?|suscr[ií]bete|newsletter|todos los derechos|aviso legal|pol[ií]tica de privacidad|comparte|compartir|'
                     r'te puede interesar|lee tambi[eé]n|m[aá]s noticias|publicidad|s[ií]guenos|whatsapp|telegram)', re.I)


def fetch_article_text(url):
    """Texto completo de la noticia (o del PDF del edicto/licitación), sin menús ni pies de página."""
    try:
        if 'news.google.com' in (url or ''):
            from .photos import resolve_google_news
            url = resolve_google_news(url)  # Google News solo es un enlace intermedio: se lee la noticia original
        r = fetch(url, timeout=30); r.raise_for_status()
        if 'pdf' in (r.headers.get('content-type') or '').lower() or r.content[:4] == b'%PDF':
            from io import BytesIO
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(r.content))
            return clean(' '.join((page.extract_text() or '') for page in reader.pages[:8]))[:18000]
        soup = BeautifulSoup(r.text, 'html.parser')
        for node in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'form', 'figure', 'noscript']): node.decompose()
        # El bloque con más párrafos largos es el cuerpo de la noticia.
        best, best_len = None, 0
        for box in soup.select('article, main, [itemprop=articleBody], .entry-content, .post-content, .article-body, .noticia, .content, body'):
            paras = [clean(p.get_text(' ', strip=True)) for p in box.find_all('p')]
            size = sum(len(p) for p in paras if len(p) > 60)
            if size > best_len:
                best, best_len = paras, size
        if best and best_len > 300:
            body = [p for p in best if len(p) > 40 and not _BOILER.search(p[:80])]
            return '\n\n'.join(dict.fromkeys(body))[:18000]
        article = soup.find('article') or soup.find('main') or soup.body
        return clean(article.get_text(' ', strip=True) if article else soup.get_text(' ', strip=True))[:18000]
    except Exception: return ''
