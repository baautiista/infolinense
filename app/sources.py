"""Source discovery: factual links for editorial review."""
import difflib
import html
import re
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

from . import db
from .config import MAX_CANDIDATE_AGE_DAYS

UA = 'InfoLinenseBot/1.0 (+editorial monitoring)'
_scan_lock = threading.Lock()
_LOCAL = re.compile(r'\b(?:la\s+l[ií]nea(?:\s+de\s+la\s+concepci[oó]n)?|linens(?:e|es)|atunara|balona|saccone)\b', re.I)
_UNRELATED = re.compile(r'\bl[ií]nea\s+(?:de\s+metro|ferroviaria|el[eé]ctrica|a[eé]rea|editorial|de\s+autob[uú]s|de\s+salida|de\s+meta)\b', re.I)

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
            items.append({'title': title, 'url': link, 'excerpt': clean(it.findtext('description')),
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
        for a in BeautifulSoup(page.text, 'html.parser').select('a[href]'):
            title = clean(a.get_text(' ', strip=True))
            match = re.match(r'^(\d{2,3}\.\d{3})\s*\.?\s*-', title)
            if not match or not exact_locality(title): continue
            doc = urljoin(link, a.get('href', ''))
            base, fragment = urldefrag(doc)
            permalink = base + '#' + (fragment + '&' if fragment else '') + 'anuncio=' + match.group(1).replace('.', '')
            results.append({'title': title[:260], 'url': permalink,
                            'excerpt': title, 'published_at': ''})
    return results

def parse_procurement(source):
    """Municipal tenders with their original publication timestamp and detail link."""
    r = fetch(source['url']); r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    results = []
    for row in soup.select('tr[data-item-type="SearchTender"]'):
        a = row.select_one('td[data-toggle-column-id="tender"] a[href]')
        if not a: continue
        date = row.select_one('td[data-toggle-column-id="call_for_tenders_published_at"]')
        try:
            published = datetime.fromtimestamp(int(date.get('data-sort-value')), timezone.utc).isoformat()
        except (AttributeError, TypeError, ValueError):
            continue  # Cannot assert that an undated tender is newly published.
        if not recent_enough(published): continue
        def field(name):
            cell = row.select_one(f'td[data-toggle-column-id="{name}"]')
            return clean(cell.get_text(' ', strip=True)) if cell else ''
        details = ' · '.join(filter(None, [field('document_number'),field('initial_amount_no_taxes'),
                                           field('process_type'), field('submission_date')]))
        results.append({'title': clean(a.get_text(' ', strip=True)),
                        'url': urljoin(source['url'], a.get('href', '')),
                        'excerpt': details, 'published_at': published})
    return results

def exact_locality(text):
    text = text or ''
    if not _LOCAL.search(text): return False
    if _UNRELATED.search(text) and not re.search(
            r'la\s+l[ií]nea\s+de\s+la\s+concepci[oó]n|linens|atunara|balona', text, re.I):
        return False
    return True

def publication_datetime(value):
    if not value: return None
    try: date = parsedate_to_datetime(value)
    except (ValueError, TypeError, IndexError):
        try: date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError: return None
    if not date.tzinfo: date = date.replace(tzinfo=timezone.utc)
    return date.astimezone(timezone.utc)

def recent_enough(value):
    date = publication_datetime(value)
    if date is None: return True  # HTML listings often omit dates; URL dedup handles repeats.
    return date >= datetime.now(timezone.utc) - timedelta(days=MAX_CANDIDATE_AGE_DAYS)

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
    if url and db.row('SELECT id FROM candidates WHERE url=?', (url,)): return True
    normalized = re.sub(r'\W+', ' ', re.sub(r'\s+[-–|]\s+[^-–|]{3,35}$', '', title).lower()).strip()
    if len(normalized) < 28: return False
    for item in db.rows('SELECT title FROM candidates ORDER BY id DESC LIMIT 300'):
        prior = re.sub(r'\W+', ' ', re.sub(r'\s+[-–|]\s+[^-–|]{3,35}$', '', item['title']).lower()).strip()
        if difflib.SequenceMatcher(None, normalized, prior).ratio() > 0.90: return True
    return False

_DATE_META = ('article:published_time', 'og:published_time', 'datePublished', 'pubdate', 'date', 'DC.date.issued',
              'article:modified_time', 'publish-date', 'sailthru.date')


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
        t = soup.find('time', attrs={'datetime': True})
        if t and publication_datetime(t['datetime']): return t['datetime']
    except Exception:
        pass
    return ''


def source_group(row):
    """Grupo de fuente para dividir el radar: Ayuntamiento, Medios, Boletines y edictos, Licitaciones, Redes, Región, Web."""
    kind = (row.get('source_kind') or row.get('kind') or '').lower()
    blob = ' '.join(str(row.get(k) or '') for k in ('source_name', 'outlet', 'url')).lower()
    if kind == 'procurement' or 'licitac' in blob or 'contratac' in blob or 'contratos' in blob: return 'Licitaciones'
    if kind == 'bop' or any(w in blob for w in ('bop', 'boe', 'boja', 'edicto')): return 'Boletines y edictos'
    if kind == 'social' or 'facebook' in blob or 'instagram' in blob: return 'Redes sociales'
    if 'lalinea.es' in blob or 'ayuntamiento' in blob: return 'Ayuntamiento'
    if any(w in blob for w in ('gibraltar.gov', 'apba', 'junta', 'diputaci')): return 'Gibraltar y región'
    if (row.get('scope') or '') in ('nacional', 'internacional'): return 'Nacional e internacional'
    if (row.get('scope') or '') == 'regional': return 'Gibraltar y región'
    return 'Medios'


def add_candidate(title, url, excerpt, source_name, source_id=None, published_at='', source_meta=None, local_angle='', outlet='', image=''):
    source_meta = source_meta or {'priority': 60, 'official': 0, 'local_scope': 0}
    title, excerpt = clean(title)[:260], clean(excerpt)[:1000]
    local_angle = clean(local_angle)[:800]
    if not title or not url or not recent_enough(published_at): return None
    if not exact_locality(title + ' ' + excerpt + ' ' + local_angle) and not source_meta.get('local_scope'): return None
    if is_duplicate(title, url): return None
    score = heuristic_score(title, excerpt + ' ' + local_angle, source_meta)
    relevance = 'low' if score < 50 else ('medium' if score < 70 else 'high')
    return db.exec_('''INSERT OR IGNORE INTO candidates(source_id,source_name,title,url,published_at,excerpt,score,relevance,status,local_angle,outlet,image_hint)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (source_id, source_name, title, url, published_at, excerpt, score, relevance, 'new', local_angle, clean(outlet)[:120], image or ''))

def read_source(source):
    if source['kind'] == 'rss': return parse_rss(source)
    if source['kind'] == 'bop': return parse_bop(source)
    if source['kind'] == 'procurement': return parse_procurement(source)
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
                    items = [i for i in items if not db.row('SELECT 1 FROM candidates WHERE url=?', (i['url'],))]
                    # Solo noticias actuales: si la lista no trae fecha, se lee de la propia página.
                    undated = [i for i in items if not i.get('published_at') and source['kind'] in ('html', 'rss', 'social')][:40]
                    if undated:
                        with ThreadPoolExecutor(max_workers=8) as dates:
                            for item, date in zip(undated, dates.map(lambda i: page_date(i['url']), undated)):
                                item['published_at'] = date
                    for item in items:
                        if not item.get('published_at') and source['kind'] in ('html', 'rss', 'social') and not source.get('local_scope'):
                            continue  # sin fecha comprobable: no se puede asegurar que sea actual
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
        archived = archive_stale()
        db.log('scan', f'Escaneo: {len(added)} nuevas; {archived} antiguas retiradas; {len(errors)} errores')
        return {'added': added, 'errors': errors, 'sources_checked': len(active), 'busy': False}
    finally:
        _scan_lock.release()

def archive_stale():
    """Retira del radar lo que ya no es actual y nadie ha marcado como útil."""
    limit = datetime.now(timezone.utc) - timedelta(days=MAX_CANDIDATE_AGE_DAYS)
    n = 0
    for row in db.rows("""SELECT id,published_at,created_at FROM candidates
                          WHERE status IN ('new','researched','needs_config') AND editorial_priority IN ('undecided','')"""):
        date = publication_datetime(row.get('published_at')) or publication_datetime((row.get('created_at') or '').replace(' ', 'T') + '+00:00')
        if date and date < limit:
            db.exec_("UPDATE candidates SET status='archived' WHERE id=?", (row['id'],)); n += 1
    return n


def fetch_article_text(url):
    if '.pdf' in urlparse(url).path.lower(): return ''
    try:
        r = fetch(url); r.raise_for_status(); soup = BeautifulSoup(r.text, 'html.parser')
        for node in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']): node.decompose()
        article = soup.find('article') or soup.find('main') or soup.body
        return clean(article.get_text(' ', strip=True) if article else soup.get_text(' ', strip=True))[:18000]
    except Exception: return ''
