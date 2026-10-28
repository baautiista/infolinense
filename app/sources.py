"""Source discovery: factual links for editorial review."""
import difflib
import json
import html
import re
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, quote, urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

from . import db
from .config import MAX_CANDIDATE_AGE_DAYS

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
_scan_lock = threading.Lock()
# La Línea de la Concepción (la ciudad), no «la línea 1 del metro», «línea de alta velocidad», etc.
_CITY = re.compile(r'(La\s+L[ií]nea\s+de\s+la\s+Concepci[oó]n|\blinens[ea]s?\b|\bLa\s+L[ií]nea\b)', re.I)
_CITY_STRONG = re.compile(r'(l[ií]nea\s+de\s+la\s+concepci[oó]n|\blinens[ea]s?\b|\batunara\b|real\s+balompédica|'
                          r'\bel\s+zabal\b|junquillos)', re.I)
# Señales inequívocas de La Línea (ganan aunque la noticia hable también de otro país)
_CITY_SURE = re.compile(r'l[ií]nea\s+de\s+la\s+concepci[oó]n|\blinens[ea]s?\b|campo\s+de\s+gibraltar', re.I)
# Latinoamérica y otros países: «La Línea» allí es otra cosa (túnel de La Línea en Colombia, barrios, cerros…)
_FOREIGN = re.compile(r'\b(colombia\w*|bogot[aá]|medell[ií]n|\bcali\b|quind[ií]o|tolima|ibagu[eé]|calarc[aá]|cajamarca|'
                      r'cundinamarca|antioquia|barranquilla|cartagena\s+de\s+indias|m[eé]xic\w*|cdmx|jalisco|monterrey|'
                      r'argentin\w*|buenos\s+aires|c[oó]rdoba,\s+argentina|chile\w*|santiago\s+de\s+chile|valpara[ií]so|'
                      r'per[uú]\b|peruan\w*|venezol\w*|caracas|ecuador\w*|quito|guayaquil|bolivia\w*|uruguay\w*|montevideo|'
                      r'paraguay\w*|guatemal\w*|hondur\w*|nicarag\w*|salvadore\w*|el\s+salvador|costa\s+rica|costarricense|panam[aá]\b|'
                      r'paname[ñn]\w*|cuban[oa]s?|rep[uú]blica\s+dominicana|dominican[oa]s?|puerto\s+rico|'
                      r'pesos?\s+(colombianos|mexicanos|argentinos|chilenos)|gobernaci[oó]n\s+de|invías|invias|'
                      r't[uú]nel\s+de\s+la\s+l[ií]nea|alto\s+de\s+la\s+l[ií]nea|carabineros)', re.I)

_CONTEXT = re.compile(r'(gibraltar|c[aá]diz|algeciras|san\s+roque|frontera|verja|ayuntamiento|juan\s+franco|poniente|levante|'
                      r'alcaidesa|cruz\s+herrera|parque\s+princesa\s+sof[ií]a|feria|velada|campo\s+de\s+gibraltar|'
                      r'san\s+bernardo|santa\s+margarita|\bbalona\b|andaluc[ií]a|junta\s+de\s+andaluc)', re.I)
_UNRELATED = re.compile(r'\bl[ií]neas?\s+(?:\d+|[a-z]\d*\b|de\s+metro|del\s+metro|ferroviaria|de\s+tren|de\s+alta|el[eé]ctrica|a[eé]rea|editorial|'
                        r'de\s+autob[uú]s|de\s+salida|de\s+meta|de\s+cr[eé]dito|roja|telef[oó]nica|de\s+fuego|de\s+defensa|de\s+ayuda|'
                        r'de\s+flotaci[oó]n|del\s+horizonte|de\s+producci[oó]n|de\s+negocio|de\s+investigaci[oó]n|de\s+trabajo|'
                        r'argumental|sucesoria|de\s+banda|de\s+cercan[ií]as|de\s+costa|blanca|caliente|directa|de\s+fondo)', re.I)
_LOCAL = _CITY
# Otros municipios: no interesan salvo que la noticia sea de La Línea
_OTHER_TOWNS = re.compile(r'\b(algeciras|san\s+roque|los\s+barrios|tarifa|jimena|castellar|c[aá]diz|jerez|chiclana|el\s+puerto\s+de\s+santa\s+mar[ií]a|'
                          r'puerto\s+real|san\s+fernando|rota|chipiona|sanl[uú]car|barbate|vejer|conil|arcos|ubrique|grazalema|medina\s+sidonia|'
                          r'paterna|bornos|villamart[ií]n|olvera|prado\s+del\s+rey|trebujena|benalup|alcal[aá]\s+de\s+los\s+gazules|estepona|marbella|'
                          r'm[aá]laga|sevilla|ceuta|sotogrande|guadiaro|pueblo\s+nuevo|taraguilla|palmones|manilva|casares)\b', re.I)
# Gibraltar solo si toca rellenos, obras, eventos, elecciones o la frontera
_GIB = re.compile(r'\bpicardo\b|\bgibraltar\b|\bpe[ñn][oó]n\b|\bllanit[oa]s?\b|gibraltare[ñn]', re.I)
# Gibraltar solo interesa con eventos, política que toque a España o rellenos que afecten a España
_GIB_EVENT = re.compile(r'(evento|festival|concierto|concert|feria|fiesta|d[ií]a\s+nacional|national\s+day|calentita|carnaval|carnival|'
                        r'gala|exposici[oó]n|marat[oó]n|espect[aá]culo|desfile|celebraci[oó]n)', re.I)
_GIB_POLITICS = re.compile(r'(picardo|gobierno\s+de\s+gibraltar|ministro\s+principal|chief\s+minister|parlamento|elecci[oó]n|elecciones|'
                           r'tratado|acuerdo|negociaci[oó]n|frontera|verja|schengen|aduana|fiscal|tabaco|brexit|soberan[ií]a|'
                           r'albares|gobierno\s+de\s+espa[ñn]a|treaty|border|frontier|customs)', re.I)
_GIB_FILL = re.compile(r'(relleno|ganad[oa]s?\s+al\s+mar|ganar\s+terreno|reclamation|reclaimed)', re.I)
_SPAIN = re.compile(r'(espa[ñn]a|espa[ñn]ol|madrid|gobierno\s+de\s+espa|ministerio|junta\s+de\s+andaluc|andaluc|c[aá]diz|'
                    r'la\s+l[ií]nea|campo\s+de\s+gibraltar|bruselas|uni[oó]n\s+europea|\bue\b|frontera|verja|transfronteriz|'
                    r'aguas|ecologistas|protesta|denuncia|spain|spanish)', re.I)
_GIB_TOPIC = re.compile(_GIB_EVENT.pattern + '|' + _GIB_POLITICS.pattern + '|' + _GIB_FILL.pattern, re.I)  # (compatibilidad)
# Comarca: lo del Campo de Gibraltar solo entra si toca a La Línea
_COMARCA = re.compile(r'campo\s+de\s+gibraltar|\bcomarca|comarcal|mancomunidad|algeciras|san\s+roque|los\s+barrios|tarifa|jimena|castellar', re.I)
_LA_LINEA_ENTITY = re.compile(r'l[ií]nea\s+de\s+la\s+concepci[oó]n|ayuntamiento\s+de\s+la\s+l[ií]nea|\blinens[ea]s?\b', re.I)
_TOWN_HALL = re.compile(r'(ayuntamiento|alcald[ií]a|consistorio)\s+de\s+([a-záéíóúñ ]{3,40}?)(?=[.,:;\-–(]|$)', re.I)

_MONTHS = {'ene': 1, 'feb': 2, 'mar': 3, 'abr': 4, 'may': 5, 'jun': 6, 'jul': 7, 'ago': 8, 'sep': 9, 'set': 9, 'oct': 10, 'nov': 11, 'dic': 12,
           'jan': 1, 'apr': 4, 'aug': 8, 'dec': 12}


def parse_es_date(text, now=None):
    """Fecha en un texto en español: 05/10/2026, 5 oct 2026, 5 de octubre de 2026, «hace 3 horas», «ayer». ISO o ''."""
    now = now or datetime.now(timezone.utc)
    t = (text or '').lower()
    m = re.search(r'\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b', t)
    if m:
        try: return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), 9, tzinfo=timezone.utc).isoformat()
        except ValueError: pass
    m = re.search(r'\b(\d{1,2})\s+(?:de\s+)?(ene|feb|mar|abr|may|jun|jul|ago|sep|set|oct|nov|dic|jan|apr|aug|dec)[a-z]*\.?(?:\s+(?:de\s+)?(\d{4}))?', t)
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

def _feed_of(source, r, soup):
    """Si la web tiene RSS (WordPress, Blogger…), se lee el RSS: trae todas las noticias con su fecha."""
    feeds = [urljoin(source['url'], l.get('href', '')) for l in soup.select('link[rel=alternate][href]')
             if 'rss' in (l.get('type') or '') or 'atom' in (l.get('type') or '')]
    feeds = [f for f in feeds if '/comments/' not in f]
    if not feeds and 'wp-content' in r.text:
        feeds = [urljoin(source['url'], '/feed/')]
    for feed in feeds[:2]:
        try:
            items = parse_rss({'url': feed})
            if items:
                return items
        except Exception:
            continue
    return []


def parse_html(source):
    r = fetch(source['url']); r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    items = _feed_of(source, r, soup)
    if items:
        return items
    for node in soup.select('nav, footer, header, aside, script, style'): node.decompose()
    links = soup.select('article a[href], h2 a[href], h3 a[href], h4 a[href], h5 a[href], h6 a[href], .elementor-post__title a[href]')
    if not links: links = soup.select('main a[href]')
    items, seen = [], set()
    host = urlparse(source['url']).hostname
    for a in links:
        title = clean(a.get_text(' ', strip=True))
        if len(title) < 28 or len(title) > 300: continue
        link = urljoin(source['url'], a.get('href', ''))
        if urlparse(link).scheme not in ('http', 'https') or (urlparse(link).hostname or '').removeprefix('www.') != (host or '').removeprefix('www.'): continue
        link, _ = urldefrag(link)
        if link in seen or link.rstrip('/') == source['url'].rstrip('/'): continue
        seen.add(link)
        parent = a.find_parent(['article', 'h2', 'h3', 'h4', 'h5', 'h6']) or a.parent
        excerpt = clean(parent.get_text(' ', strip=True) if parent else title)[:900]
        items.append({'title': title, 'url': link, 'excerpt': excerpt, 'published_at': ''})
        if len(items) >= 100: break
    return items

def parse_bop(source):
    """Read local notices from the latest provincial bulletins (usually two to three weeks)."""
    r = fetch(source['url']); r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    bulletins = []
    for a in soup.select('a[href]'):
        link = urljoin(source['url'], a.get('href', ''))
        if '/boletin/Boletin-numero-' in link and link not in bulletins: bulletins.append(link)
        if len(bulletins) == 5: break
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
            if not match or not tender_ok(title): continue
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
            r'\b\d{1,2}[/.-]\d{1,2}[/.-]\d{4}\b|\b\d{1,2}\s+(?:de\s+)?(?:ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic|jan|apr|aug|dec)[a-z]*\.?\s+(?:de\s+)?\d{4}', text.lower())]
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

def parse_licitacionesio(source):
    """licitaciones.io: tarjetas con enlace /licitacion/…, fechas de publicación y plazo, presupuesto."""
    r = fetch(source['url'], timeout=30); r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    out, seen = [], set()
    now = datetime.now(timezone.utc)
    for a in soup.find_all('a', href=re.compile(r'/licitacion/[^/?#]+')):
        url = urljoin(source['url'], a['href'].split('?')[0])
        if url in seen: continue
        card = a
        for _ in range(6):  # sube hasta la tarjeta que tiene las fechas
            if card.parent is None or len(re.findall(r'\d{1,2}/\d{1,2}/\d{4}', card.get_text(' ', strip=True))) >= 1:
                break
            card = card.parent
        text = clean(card.get_text(' ', strip=True))
        title = clean(a.get_text(' ', strip=True)) or clean((card.find(['h2', 'h3', 'h4']) or a).get_text(' ', strip=True))
        if len(title) < 12: continue
        dates = re.findall(r'\d{1,2}/\d{1,2}/\d{4}', text)
        published = _dmy(dates[0]) if dates else ''
        closing = publication_datetime(_dmy(dates[1])) if len(dates) > 1 else None
        if not published: continue
        still_open = closing is not None and closing >= now
        if not still_open and not recent_enough(published, WINDOW_DAYS['Licitaciones y edictos']): continue
        amount = re.search(r'\d[\d.,]*\s*€|€\s*\d[\d.,]*', text)
        excerpt = ' · '.join(x for x in [('Presupuesto: ' + amount.group(0)) if amount else '',
                                         ('Plazo hasta ' + closing.strftime('%d/%m/%Y')) if closing else ''] if x)
        seen.add(url)
        out.append({'title': 'Licitación: ' + title[:240], 'url': url, 'excerpt': excerpt or text[:400], 'published_at': published})
    if not out and '/licitacion/' not in r.text:
        raise ValueError('licitaciones.io no mostró licitaciones (puede haber cambiado de formato)')
    return out


PLACSP_FEED = 'https://contrataciondelestado.es/sindicacion/sindicacion_643/licitacionesPerfilesContratanteCompleto3.atom'
PLACSP_MENORES = 'https://contrataciondelestado.es/sindicacion/sindicacion_1143/contratosMenoresPerfilesContratantes.atom'
_LINEA = re.compile(r'L[ií]nea de la Concepci[oó]n', re.I)
PLACSP_STATES = {'PRE': 'Anuncio previo', 'PUB': 'En plazo', 'EV': 'Pendiente de adjudicación', 'ADJ': 'Adjudicada',
                 'RES': 'Formalizada', 'ANUL': 'Anulada'}
_STATE_TITLE = {'PRE': 'Licitación (anuncio previo): ', 'PUB': 'Licitación: ', 'EV': 'Licitación cerrada, pendiente de adjudicar: ',
                'ADJ': 'Adjudicada: ', 'RES': 'Formalizada: ', 'ANUL': 'Anulada: '}


def _tag(raw, pattern):
    m = re.search(pattern, raw, re.S)
    return clean(re.sub(r'<[^>]+>', ' ', m.group(1))) if m else ''


def parse_placsp(source, remember_state=True):
    """Plataforma de Contratación del Sector Público (fuente abierta oficial, Atom). Se queda con lo de La Línea y avisa de:
    licitaciones nuevas, cada cambio de estado (cerrada, adjudicada, formalizada, anulada), rectificaciones o documentos
    nuevos, y contratos menores."""
    url = source.get('url') or ''
    menores = 'menores' in url.lower() or 'sindicacion_1143' in url
    if 'sindicacion' not in url:
        url = PLACSP_FEED  # el enlace del perfil del contratante no se puede leer: se usa la fuente abierta oficial
    r = requests.get(url, timeout=(15, 90), headers={'User-Agent': UA, 'Accept': 'application/atom+xml,application/xml'})
    r.raise_for_status()
    text = r.content.decode('utf-8', 'ignore')
    if '<entry' not in text:
        raise ValueError('La Plataforma de Contratación no devolvió licitaciones')
    db.exec_('CREATE TABLE IF NOT EXISTS placsp_state(id TEXT PRIMARY KEY, estado TEXT, updated TEXT)')
    out = []
    now = datetime.now(timezone.utc)
    for raw in re.findall(r'<entry\b.*?</entry>', text, re.S):
        plain = clean(re.sub(r'<[^>]+>', ' ', raw))
        if not _LINEA.search(plain):
            continue
        title = _tag(raw, r'<title[^>]*>(.*?)</title>')
        link = (re.search(r'<link[^>]*href="([^"]+)"', raw) or [None, ''])[1].replace('&amp;', '&')
        uid = _tag(raw, r'<id>(.*?)</id>') or link
        updated = _tag(raw, r'<updated>(.*?)</updated>')
        summary = _tag(raw, r'<summary[^>]*>(.*?)</summary>')
        if not title or not link:
            continue
        estado = (re.search(r'Estado:\s*([A-Z]+)', summary) or re.search(r'ContractFolderStatusCode[^>]*>\s*([A-Z]+)\s*<', raw) or [None, ''])[1]
        winner = _tag(raw, r'<cac:WinningParty>.*?<cbc:Name>(.*?)</cbc:Name>')
        awarded = _tag(raw, r'<cac:AwardedTenderedProject>.*?<cbc:PayableAmount[^>]*>(.*?)</cbc:PayableAmount>')
        budget = _tag(raw, r'<cbc:TaxExclusiveAmount[^>]*>(.*?)</cbc:TaxExclusiveAmount>') or _tag(raw, r'<cbc:TotalAmount[^>]*>(.*?)</cbc:TotalAmount>')
        deadline = _tag(raw, r'<cac:TenderSubmissionDeadlinePeriod>.*?<cbc:EndDate>(.*?)</cbc:EndDate>')
        # El feed estatal no siempre vuelve a publicar una licitación que sigue abierta.
        # Conservamos los expedientes con plazo vigente aunque su última actualización
        # tenga más de 20 días; los cerrados y contratos menores sí pasan por la ventana.
        open_tender = estado in ('PUB', 'PRE') and _deadline_open(deadline, now)
        if not open_tender and not recent_enough(updated, WINDOW_DAYS['Licitaciones y edictos']):
            continue
        facts = [PLACSP_STATES.get(estado, estado)] if estado else []
        if budget: facts.append(f'Presupuesto: {budget} € sin IVA')
        if deadline and estado in ('PUB', 'PRE'): facts.append(f'Plazo hasta {deadline}')
        if winner: facts.append(f'Adjudicataria: {winner}')
        if awarded: facts.append(f'Importe de adjudicación: {awarded} €')
        organ = (re.search(r'[ÓO]rgano de Contrataci[oó]n:\s*([^;]+)', summary) or [None, ''])[1].strip()
        excerpt = ((organ + '. ' if organ else '') + '. '.join(facts) + '. ' + summary)[:700]
        prev = db.row('SELECT estado,updated FROM placsp_state WHERE id=?', (uid,))
        if remember_state:
            db.exec_('INSERT OR REPLACE INTO placsp_state(id,estado,updated) VALUES(?,?,?)', (uid, estado, updated))
        if menores:
            if prev and prev['updated'] == updated:
                continue
            if prev:
                head, key = 'Actualización de contrato menor: ', 'menor-' + updated[:16]
            else:
                head, key = 'Contrato menor: ', 'menor'
        elif prev and prev['estado'] == estado:
            if prev['updated'] == updated:
                continue  # sin cambios
            head, key = 'Cambios en la licitación: ', estado + '-' + updated[:16]  # rectificación, documentos nuevos, plazo…
        else:
            head, key = _STATE_TITLE.get(estado, 'Licitación: '), estado or 'nuevo'
        out.append({'title': head + title[:240], 'url': link + '#' + key, 'excerpt': excerpt, 'published_at': updated})
    return out


def _dmy(value):
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', value or '')
    if not m: return ''
    return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), 9, 0, tzinfo=timezone.utc).isoformat()


_EXPIRED = re.compile(r'sesi[oó]n ha expirado|vuelva a iniciar sesi[oó]n|espere un momento por favor', re.I)


_sede = {'session': None, 'at': 0}


def sede_get(url):
    """GET en la sede electrónica con sesión (cookie) reutilizada; la renueva si caduca."""
    if _sede['session'] is None or time.time() - _sede['at'] > 900:
        fetch_edictos_html('https://www.sedeelectronica.lalinea.es/edictos/edicto/buscar-edictos-filtro-pub?primeraBusqueda=true', keep=True)
    r = _sede['session'].get(url, timeout=30)
    if not r.ok or _EXPIRED.search(r.text[:20000] if 'text' in (r.headers.get('content-type') or 'text') else ''):
        fetch_edictos_html('https://www.sedeelectronica.lalinea.es/edictos/edicto/buscar-edictos-filtro-pub?primeraBusqueda=true', keep=True)
        r = _sede['session'].get(url, timeout=30)
    r.raise_for_status()
    return r


def fetch_edictos_html(url, keep=False):
    """La sede pide una sesión (cookie): se abre primero el tablón, se siguen sus redirecciones y luego se lee el listado."""
    s = requests.Session()
    s.headers.update({'User-Agent': UA, 'Accept-Language': 'es-ES,es;q=0.9',
                      'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'})
    parsed = urlparse(url)
    root = '%s://%s/' % (parsed.scheme, parsed.netloc)
    base = url.split('/edicto/')[0].rstrip('/') + '/' if '/edicto/' in url else root
    last = None
    # Entrada pública del tablón (abre sesión sola y devuelve el listado): la que enlaza la propia sede
    public = root + 'edictos/publico?idOrgan=23'
    try:
        first = s.get(public, timeout=30)
        if first.ok and not _EXPIRED.search(first.text[:20000]) and 'codigo=' in first.text:
            if keep: _sede.update(session=s, at=time.time())
            return first.text
    except requests.RequestException:
        pass
    for attempt in range(3):
        try:
            last = s.get(url, timeout=30, headers={'Referer': base})
            if last.ok and not _EXPIRED.search(last.text[:20000]):
                if keep: _sede.update(session=s, at=time.time())
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


def edictos_from_search():
    """Plan B si la sede no deja entrar: edictos de la sede que encuentra el buscador (últimos 20 días)."""
    from . import social
    out, seen = [], set()
    for q in ('site:sedeelectronica.lalinea.es edicto', 'site:sedeelectronica.lalinea.es/edictos'):
        for it in social.web_search(q, days=20, limit=20):
            url = re.sub(r';jsessionid=[^?#]+', '', it.get('url') or '', flags=re.I)
            if 'sedeelectronica.lalinea.es' not in url or url in seen: continue
            seen.add(url)
            title = clean(re.sub(r'\s*[-|·]\s*Sede electr[oó]nica.*$', '', it.get('title') or '', flags=re.I))
            # Si el buscador no trae fecha, no inventamos que el edicto sea de hoy.
            indexed_date = it.get('published_at') or ''
            date = indexed_date if publication_datetime(indexed_date) else parse_es_date(it.get('snippet') or '')
            if not date:
                continue
            if title and recent_enough(date, WINDOW_DAYS['Licitaciones y edictos']):
                out.append({'title': 'Edicto: ' + title[:240], 'url': url, 'excerpt': clean(it.get('snippet') or title), 'published_at': date})
    return out


def parse_edictos(source):
    """Tablón de edictos de la sede electrónica: tabla con fecha de publicación, fin, título y enlace."""
    try:
        page = fetch_edictos_html(source['url'])
    except Exception as exc:
        found = edictos_from_search()
        if found:
            return found
        raise ValueError('La sede no deja leer el tablón desde el servidor (%s) y el buscador no muestra edictos recientes.' % str(exc)[:120])
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
        # La sede ha cambiado varias veces cómo abre cada edicto: a veces el
        # enlace no contiene "codigo=" ni "edicto", o la fila solo ofrece un
        # botón. No descartamos por eso una publicación oficial con fecha y título.
        link = row.find('a', href=re.compile(r'codigo=|edicto|\.pdf(?:$|[?#])', re.I))
        if link is None:
            link = next((a for a in row.find_all('a', href=True)
                         if a.get('href', '').strip() and not a.get('href', '').strip().lower().startswith(('javascript:', '#', 'mailto:'))), None)
        title = ''
        for c in cells:
            text = clean(c.get_text(' ', strip=True))
            if len(text) > len(title) and not re.fullmatch(r'[\d/ :.-]+', text): title = text
        published = _dmy(row.get_text(' ', strip=True))  # la primera fecha de la fila es la de publicación
        href = link.get('href', '') if link else ''
        if not href:
            # El listado público sigue permitiendo localizar el anuncio cuando
            # la sede oculta su enlace de detalle tras JavaScript.
            listing = urlparse(source['url'])
            public_page = f'{listing.scheme}://{listing.netloc}/edictos/publico?idOrgan=23'
            href = public_page + '#' + quote((published[:10] + '-' + title[:100]), safe='')
        add(title, href, published)
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
    if not out:
        # El tablón puede cambiar el HTML o paginar por JavaScript. El buscador
        # actúa como respaldo, limitado a enlaces del dominio oficial y con fecha.
        return edictos_from_search()
    return out


def foreign(text):
    """Noticia de otro país (sobre todo Latinoamérica) sin nada que la ate de verdad a La Línea de la Concepción."""
    text = text or ''
    return bool(_FOREIGN.search(text)) and not _CITY_SURE.search(text)


def exact_locality(text):
    """Solo la ciudad de La Línea de la Concepción."""
    text = text or ''
    if foreign(text):
        return False
    if _CITY_STRONG.search(text):
        return True
    # «La Línea» a secas: con mayúscula y con contexto local, y sin ser una línea de metro, tren, etc.
    if not re.search(r'\bLa\s+L[ií]nea\b', text):
        return False
    if _UNRELATED.search(text):
        return False
    return bool(_CONTEXT.search(text))

def gibraltar_topic(text):
    """Gibraltar interesa solo si son eventos, política relacionada con España o rellenos que afectan a España."""
    t = re.sub(r'campo\s+de\s+gibraltar|estrecho\s+de\s+gibraltar|bah[ií]a\s+de\s+(algeciras|gibraltar)', ' ', text or '', flags=re.I)
    if not _GIB.search(t):
        return False
    spain = _SPAIN.search(re.sub(r'\bgibraltar\w*', ' ', t, flags=re.I))
    return bool(_GIB_EVENT.search(t) or (_GIB_POLITICS.search(t) and spain) or (_GIB_FILL.search(t) and spain))


# Sitios que no son La Línea (para descartar publicaciones de redes de otros pueblos: Nerja, Marbella…)
_ELSEWHERE = re.compile(r'\b(nerja|m[aá]laga|marbella|estepona|fuengirola|torremolinos|benalm[aá]dena|mijas|manilva|casares|'
                        r'sotogrande|ronda|antequera|v[eé]lez|torrox|frigiliana|almu[nñ][eé]car|motril|rinc[oó]n\s+de\s+la\s+victoria|'
                        r'ceuta|melilla|sevilla|granada|almer[ií]a|huelva|ja[eé]n|c[oó]rdoba|madrid|barcelona|valencia|alicante|murcia|'
                        r'benidorm|torrevieja|zaragoza|bilbao|vigo|coru[nñ]a|palma|ibiza|tenerife|las\s+palmas|lanzarote|'
                        r'puerto\s+real|san\s+fernando|rota|chipiona|sanl[uú]car|conil|barbate|vejer|arcos|ubrique|olvera|medina|'
                        r'chiclana|jerez|c[aá]diz\s+capital)\b', re.I)
_LOCAL_SPECIFIC = re.compile(r'(gibraltar|frontera|verja|juan\s+franco|alcaidesa|cruz\s+herrera|parque\s+princesa\s+sof[ií]a|velada|'
                             r'san\s+bernardo|santa\s+margarita|\bbalona\b|atunara|zabal|junquillos|conchal|poniente\s+de\s+la\s+l[ií]nea|'
                             r'calle\s+real|plaza\s+de\s+la\s+iglesia|ayuntamiento\s+de\s+la\s+l[ií]nea|polideportivo\s+municipal|'
                             r'barriada|campo\s+de\s+gibraltar)', re.I)


def other_place(text):
    """Habla de otro municipio (Ayuntamiento de X, Nerja, Algeciras…) sin señal fuerte de La Línea."""
    text = re.sub(r'\((?:provincia\s+de\s+)?c[aá]diz\)|,\s*c[aá]diz\b|(?:provincia|diputaci[oó]n|bop|diario)\s+de\s+c[aá]diz|bop\s+c[aá]diz',
                  ' ', text or '', flags=re.I)
    if _CITY_STRONG.search(text):
        return False
    if _ELSEWHERE.search(text) or _OTHER_TOWNS.search(text):
        return True
    if any(not re.search(r'la\s+l[ií]nea', m.group(2), re.I) for m in _TOWN_HALL.finditer(text)):
        return True
    from . import gazetteer
    return bool(gazetteer.places_in(text))  # cualquier municipio de España o país del mundo (Cehegín, Rota, Lima…)


def strict_local(text):
    """Para redes sociales: tiene que ser claramente de La Línea de la Concepción."""
    text = text or ''
    if foreign(text) or other_place(text):
        return False
    if _CITY_STRONG.search(text):
        return True
    return bool(re.search(r'\bLa\s+L[ií]nea\b', text) and not _UNRELATED.search(text) and _LOCAL_SPECIFIC.search(text))


def place_ok(text, extra=''):
    """¿Interesa a InfoLinense? Solo La Línea; de Gibraltar, lo que afecta (rellenos, obras, eventos, elecciones, frontera)."""
    text = text or ''
    if foreign(text):
        return False
    if exact_locality(text):
        return True
    if re.search(r'\bLa\s+L[ií]nea\b', text) and not _UNRELATED.search(text):
        from . import brands
        if brands.classify(text):  # «La Línea» + hermandades o carnaval: es de la ciudad
            return True
    return gibraltar_topic(text + ' ' + (extra or ''))


def tender_ok(title, excerpt=''):
    """Edicto o licitación: tiene que ser de La Línea, no de otro ayuntamiento."""
    head = (title or '') + ' ' + (excerpt or '')[:300]
    if not _LA_LINEA_ENTITY.search(head):
        return False
    for m in _TOWN_HALL.finditer(title or ''):  # «Ayuntamiento de Algeciras…» en el titular
        if not re.search(r'la\s+l[ií]nea', m.group(2), re.I):
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


def _deadline_open(value, now=None):
    """True when a tender deadline (ISO or Spanish date) has not passed."""
    raw = clean(value or '')
    now = now or datetime.now(timezone.utc)
    date = publication_datetime(raw) or publication_datetime(parse_es_date(raw))
    if date is None:
        return False
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{4}', raw):
        return date + timedelta(days=1) > now  # una fecha sin hora incluye el día entero
    return date >= now


def _open_tender_deadline(title, excerpt, now=None):
    """Keep open tenders visible beyond the normal freshness window."""
    if not re.search(r'\blicitaci[oó]n\b', title or '', re.I):
        return False
    date = re.search(r'plazo\s+hasta\s*:?\s*(\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?|\d{1,2}[/.-]\d{1,2}[/.-]\d{4})', excerpt or '', re.I)
    return bool(date and _deadline_open(date.group(1), now))

# Días que una pieza sigue siendo actual según su bloque
WINDOW_DAYS = {'Licitaciones y edictos': 20, 'Ayuntamiento': 7}


def recent_enough(value, days=None):
    date = publication_datetime(value)
    if date is None: return True  # HTML listings often omit dates; URL dedup handles repeats.
    return date >= datetime.now(timezone.utc) - timedelta(days=days or MAX_CANDIDATE_AGE_DAYS)


def published_this_week(value):
    """Whether a dated item was published in the current Monday-to-Sunday week in Spain."""
    date = publication_datetime(value)
    if date is None:
        return False
    from zoneinfo import ZoneInfo
    madrid = ZoneInfo('Europe/Madrid')
    today = datetime.now(madrid).date()
    local_date = date.astimezone(madrid).date()
    week_start = today - timedelta(days=today.weekday())
    return week_start <= local_date < week_start + timedelta(days=7)

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
        r = fetch(url, timeout=8)
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


BLOCKS = ['Ayuntamiento', 'Licitaciones y edictos', 'Instituciones y empresas', 'Otros medios', 'Nacionales adaptables']


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
    if name.startswith('primarias'):
        return 'Instituciones y empresas'  # Junta, Diputación, Estado, Gibraltar, empresas: antes que los periódicos
    if (row.get('scope') or '') in ('nacional', 'internacional', 'regional') or 'nacional' in name:
        return 'Nacionales adaptables'
    return 'Otros medios'


import os as _os
SITE_URL = _os.getenv('INFOLINENSE_SITE_URL', 'https://infolinense.com').rstrip('/')
_site_cache = {'at': 0, 'titles': []}


def site_titles():
    """Titulares ya publicados en infolinense.com (RSS, mapa del sitio o portada). Se guardan 30 minutos."""
    if time.time() - _site_cache['at'] < 1800:
        return _site_cache['titles']
    titles = []
    for path in ('/rss.xml', '/feed', '/sitemap.xml', '/'):
        try:
            r = fetch(SITE_URL + path, timeout=12)
            if not r.ok:
                continue
            text = r.text
            if '<item' in text:
                titles += [clean(t) for t in re.findall(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', text)[1:]]
            elif '<urlset' in text:  # las direcciones llevan el titular: /noticia/abre-el-plazo-de-...-123
                for loc in re.findall(r'<loc>(.*?)</loc>', text)[:600]:
                    slug = loc.rstrip('/').rsplit('/', 1)[-1]
                    slug = re.sub(r'-\d+$', '', slug)
                    if slug.count('-') >= 3:
                        titles.append(slug.replace('-', ' '))
            else:
                soup = BeautifulSoup(text, 'html.parser')
                titles += [clean(h.get_text(' ', strip=True)) for h in soup.select('h1, h2, h3, article a')][:200]
            if len(titles) >= 5:
                break
        except Exception:
            continue
    _site_cache.update(at=time.time(), titles=[t for t in titles if len(t) > 15][:800])
    return _site_cache['titles']


def similar_to_published(title):
    """¿InfoLinense ya lo ha publicado (desde el panel o directamente en la web)?"""
    norm = lambda t: re.sub(r'\W+', ' ', (t or '').lower()).strip()
    words = set(w for w in norm(title).split() if len(w) > 3)
    if len(words) < 3: return False
    published = [a['headline'] for a in db.rows("SELECT headline FROM articles WHERE status IN ('approved','published') "
                                                "AND updated_at>=datetime('now','-60 day') ORDER BY id DESC LIMIT 400")]
    for other_title in published + site_titles():
        other = set(w for w in norm(other_title).split() if len(w) > 3)
        if other and len(words & other) / max(1, min(len(words), len(other))) >= 0.7:
            return True
        if same_story(title, other_title):
            return True
    return False


_STORY_STOP = set('''para como pero desde hasta sobre entre tras ante bajo contra durante mediante segun este esta estos estas
ese esos esas aquel todo toda todos todas otro otra otros otras nuestro nueva nuevo nuevas nuevos donde cuando quien quienes cual
cuales tambien muy mas menos han hay ser sido sera fue son estan tiene tienen hace hacen linea concepcion linense linenses
ayuntamiento municipal ciudad vecinos edicto licitacion noticia hoy ayer manana tras'''.split())
_TENDER = re.compile(r'licitaci|adjudica|edicto|contrataci', re.I)


# raíces que cambian según el medio («detenido»/«detiene», «robo»/«robar»…)
_SAME_ROOT = {'deten': 'deten', 'detie': 'deten', 'robar': 'rob', 'robos': 'rob', 'roban': 'rob', 'roba': 'rob',
              'falle': 'muert', 'muere': 'muert', 'muert': 'muert', 'herid': 'herid', 'inaug': 'inaug', 'arran': 'inici',
              'comie': 'inici', 'empie': 'inici'}


@lru_cache(maxsize=20000)
def _story_tokens(title):
    t = re.sub(r'\s+[-–|]\s+[^-–|]{3,40}$', '', title or '')  # quita « - Europa Sur»
    t = unicodedata.normalize('NFD', t.lower())
    t = ''.join(c for c in t if unicodedata.category(c) != 'Mn')
    out = set()
    for w in re.findall(r'[a-z0-9ñ]+', t):
        if w.isdigit():
            if not re.fullmatch(r'20[12]\d', w): out.add(w)  # las cifras distinguen noticias; los años no
        elif len(w) >= 4 and w not in _STORY_STOP:
            out.add(_SAME_ROOT.get(w[:5], w[:6]))
    return frozenset(out)


def same_story(a, b):
    """¿Dos titulares cuentan la misma noticia? (palabras clave en común, sin contar «La Línea», años, etc.)"""
    ta, tb = _story_tokens(a), _story_tokens(b)
    if min(len(ta), len(tb)) < 3: return False
    shared = len(ta & tb)
    if shared >= 3 and shared / min(len(ta), len(tb)) >= 0.4:
        return True
    if shared >= 2 and shared / min(len(ta), len(tb)) >= 0.5:  # mismo tema con titulares cortos y distintos
        return True
    norm = lambda t: ' '.join(sorted(_story_tokens(t)))
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio() >= 0.8


def story_match(a, ea, b, eb):
    """Misma noticia contada por distintos medios: por el titular o, si los titulares difieren, por titular + entradilla."""
    if same_story(a, b):
        return True
    ta, tb = _story_tokens(a), _story_tokens(b)
    if len(ta & tb) < 2:
        return False
    xa, xb = _story_tokens(a + ' ' + (ea or '')[:600]), _story_tokens(b + ' ' + (eb or '')[:600])
    shared = len(xa & xb)
    return shared >= 5 and shared / max(1, min(len(xa), len(xb))) >= 0.35  # mismo tema aunque lo cuenten distinto


def find_story(title, exclude=None, days=7, excerpt=''):
    """Noticia abierta del radar (últimos días) que cuenta lo mismo."""
    if _TENDER.search(title or ''): return None  # cada edicto o licitación es un documento distinto
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    for c in db.rows("""SELECT id,title,excerpt FROM candidates WHERE status NOT IN ('archived','rejected','merged')
                        AND created_at>=? ORDER BY id""", (since,)):
        if c['id'] != exclude and not _TENDER.search(c['title'] or '') and story_match(title, excerpt, c['title'], c.get('excerpt')):
            return c['id']
    return None


def find_tender(title, days=45):
    """La misma licitación vista en otra web (Gobierto, licitaciones.io, BOP…). Los edictos no se unen nunca."""
    if not re.match(r'\s*licitaci', title or '', re.I):
        return None
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    clean_t = lambda t: re.sub(r'^\s*licitaci[oó]n\s*(de|:)?\s*', '', t or '', flags=re.I)
    mine = _story_tokens(clean_t(title))
    for c in db.rows("""SELECT id,title FROM candidates WHERE status NOT IN ('rejected','merged') AND created_at>=?
                        AND title LIKE 'Licitaci%' ORDER BY id""", (since,)):
        other = _story_tokens(clean_t(c['title']))
        shared = len(mine & other)
        if shared >= 4 and shared / max(1, min(len(mine), len(other))) >= 0.7:
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
    rows = db.rows("""SELECT c.*,(SELECT id FROM articles a WHERE a.candidate_id=c.id AND a.status!='rejected') article_id,
                             COALESCE(s.official,0) AND COALESCE(s.local_scope,0) official_local
                      FROM candidates c LEFT JOIN sources s ON s.id=c.source_id
                      WHERE c.status NOT IN ('archived','rejected','merged') AND c.created_at>=? ORDER BY c.id""", (since,))
    rows = [r for r in rows if not _TENDER.search(r['title'] or '') and r.get('social_type') in (None, '')]
    gone, merged = set(), 0
    for r in rows:  # lo pendiente que ya decidiste desde otro medio: se une a la decidida (salvo cambio importante)
        if r.get('article_id') or r.get('official_local') or (r.get('editorial_priority') or 'undecided') not in ('undecided', '') \
                or r['status'] not in ('new', 'needs_config', 'researched') or r.get('update_of'):
            continue
        d = find_decided(r['title'], r.get('excerpt') or '')
        if d and d['id'] != r['id'] and not important_change(d['title'], d.get('excerpt'), r['title'], r.get('excerpt')):
            attach_link(d['id'], r['title'], r['url'], r.get('excerpt') or '', r.get('source_name') or '', r.get('outlet') or '', r.get('published_at') or '')
            db.exec_('UPDATE candidate_links SET candidate_id=? WHERE candidate_id=?', (d['id'], r['id']))
            db.exec_("UPDATE candidates SET status='merged',merged_into=? WHERE id=?", (d['id'], r['id']))
            gone.add(r['id']); merged += 1
    rows = [r for r in rows if r['id'] not in gone]
    rank = lambda r: (1 if r.get('article_id') else 0, 1 if (r.get('editorial_priority') or 'undecided') not in ('undecided', '') else 0, -r['id'])
    for i, a in enumerate(rows):
        if a['id'] in gone: continue
        for b in rows[i + 1:]:
            if b['id'] in gone or not story_match(a['title'], a.get('excerpt'), b['title'], b.get('excerpt')): continue
            keep, drop = (a, b) if rank(a) >= rank(b) else (b, a)
            if drop.get('official_local') and not keep.get('official_local'):
                keep, drop = drop, keep  # la del Ayuntamiento manda; la del otro medio se une a ella
            if drop.get('official_local') or drop.get('article_id') or drop['status'] not in ('new', 'needs_config', 'researched'):
                continue  # algo ya en redacción no se toca
            attach_link(keep['id'], drop['title'], drop['url'], drop.get('excerpt') or '', drop.get('source_name') or '',
                        drop.get('outlet') or '', drop.get('published_at') or '')
            db.exec_('UPDATE candidate_links SET candidate_id=? WHERE candidate_id=?', (keep['id'], drop['id']))
            db.exec_("UPDATE candidates SET status='merged',merged_into=? WHERE id=?", (keep['id'], drop['id']))
            gone.add(drop['id']); merged += 1
            if drop is a: break
    return merged


_deep = {'n': 0}
DEEP_LIMIT = 15  # comprobaciones de texto completo por búsqueda (noticias comarcales)


def comarca_angle(url, text_hint=''):
    """Noticia del Campo de Gibraltar: ¿el texto completo nombra a La Línea? Devuelve la frase de La Línea o ''."""
    if _deep['n'] >= DEEP_LIMIT:
        return ''
    _deep['n'] += 1
    text = fetch_article_text(url) or ''
    for sentence in re.split(r'(?<=[.!?])\s+', text):
        if _LA_LINEA_ENTITY.search(sentence) or exact_locality(sentence):
            return clean(sentence)[:300]
    return ''


ADAPT_PREFIX = 'Nacionales adaptables · Temas'
# Temas nacionales que se pueden contar en clave local («suben los alquileres» → cómo afecta en La Línea).
_ADAPT_TOPICS = {
    'vivienda y precios': r'alquiler\w*|vivienda\w*|hipotec\w*|eur[ií]bor|ipc|inflaci[oó]n|precios?\s+de\s+(la\s+)?(luz|gasolina|vivienda|alimentos|la\s+compra)|'
                          r'factura\s+de\s+la\s+luz|recibo\s+de\s+la\s+luz|tarifa\s+el[eé]ctrica|electricidad|gasolina|di[eé]sel|carburantes?|butano|'
                          r'cesta\s+de\s+la\s+compra|supermercados?|alimentos|aceite\s+de\s+oliva',
    'empleo y paro': r'paro|parados|desempleo|desemplead\w*|\bepa\b|afiliaci[oó]n|afiliados|seguridad\s+social|salario\s+m[ií]nimo|\bsmi\b|'
                     r'salarios?|sueldos?|aut[oó]nomos|contratos?\s+(temporales|indefinidos|fijos)|\berte\b|empleo|ofertas?\s+de\s+empleo|sepe|oposiciones',
    'rentas, impuestos y ayudas': r'rentas?|irpf|hacienda|impuestos?|\biva\b|declaraci[oó]n\s+de\s+la\s+renta|pensi[oó]n\w*|pensionistas|jubilaci[oó]n|'
                                  r'ingreso\s+m[ií]nimo|\bimv\b|ayudas?|subsidios?|prestaci[oó]n\w*|becas?|bono\s+(social|alquiler|joven|cultural)|'
                                  r'cheque|deducci[oó]n\w*|pobreza|exclusi[oó]n\s+social',
    'economía': r'econom[ií]a|econ[oó]mic\w*|\bpib\b|tipos?\s+de\s+inter[eé]s|\bbce\b|banco\s+de\s+espa[nñ]a|consumo|comercio|peque[nñ]o\s+comercio|'
                r'hosteler[ií]a|turismo|tur[ií]stas|rebajas|black\s+friday|ventas|exportaciones|morosidad|cr[eé]ditos?|ahorro|bancos?|comisiones',
    'medio ambiente': r'medio\s?ambiente|medioambiental\w*|clima|clim[aá]tic\w*|ola\s+de\s+calor|calor|sequ[ií]a|incendios?|contaminaci[oó]n|'
                      r'calidad\s+del\s+aire|reciclaje|residuos|basura|pl[aá]sticos?|playas?|costas?|litoral|vertidos?|emisiones|'
                      r'renovables|placas\s+solares|autoconsumo|aemet|alertas?\s+(amarilla|naranja|roja)|temporal|lluvias|tormentas?',
    'servicios y día a día': r'\bdgt\b|itv|multas?|radares?|carn[eé]\s+de\s+conducir|sanidad|sanitari\w*|m[eé]dicos|listas?\s+de\s+espera|'
                             r'atenci[oó]n\s+primaria|vacun\w*|gripe|farmacias?|medicamentos?|colegios?|curso\s+escolar|educaci[oó]n|universidad\w*|'
                             r'huelgas?|renfe|trenes|autob[uú]s|transporte\s+p[uú]blico|cambio\s+de\s+hora|festivos?|puentes?|permisos?|bajas?\s+laborales?|'
                             r'okupa\w*|estafas?|consumidores|correos|dependencia|mayores|j[oó]venes',
}
_ADAPT_TOPIC = re.compile(r'\b(' + '|'.join('(?:%s)' % v for v in _ADAPT_TOPICS.values()) + r')\b', re.I)
# Lo que no se adapta: otras comunidades o ciudades concretas, bolsa, deportes, sucesos, famosos, pelea política y el extranjero.
_ADAPT_NO = re.compile(r'\b(madrid|barcelona|catalu\w+|valencia\w*|pa[ií]s\s+vasco|euskadi|galicia|asturias|murcia|arag[oó]n|zaragoza|'
                       r'bilbao|canarias|baleares|mallorca|ibiza|navarra|castilla|extremadura|cantabria|rioja|toledo|valladolid|'
                       r'alicante|m[aá]laga|sevilla|granada|c[oó]rdoba|almer[ií]a|huelva|ja[eé]n|nerja|marbella|benidorm|'
                       r'ibex|bolsa|cotiza\w*|acciones|dividendo|beneficio\s+neto|\bopa\b|'
                       r'f[uú]tbol|liga|champions|real\s+madrid|bar[cç]a|atl[eé]tico|tenis|f[oó]rmula\s+1|motogp|baloncesto|'
                       r'asesin\w*|cr[ií]men|detenid\w*|juicio|tribunal\s+supremo|imputad\w*|famos\w*|boda|reality|gran\s+hermano|supervivientes|'
                       r'feij[oó]o|abascal|puigdemont|amnist[ií]a|moci[oó]n\s+de\s+censura|'
                       r'trump|putin|ucrania|gaza|israel|eeuu|estados\s+unidos|francia|alemania|china|rusia|marruecos)\b', re.I)
ADAPT_ANGLE = ('ENFOQUE LA LÍNEA (NACIONAL ADAPTADA): es una noticia de ámbito nacional. No la copies: cuenta cómo afecta a los vecinos '
               'de La Línea (a quién afecta, cuánto, desde cuándo, qué hay que hacer o dónde pedirlo). El titular se centra en La Línea '
               '(p. ej. «Así afecta a La Línea la subida de…», «El paro en La Línea…»). Busca el dato de La Línea si existe (paro registrado del '
               'municipio, renta media del INE, precio del alquiler en la ciudad, oficina del SEPE o de la Seguridad Social, centro de salud, '
               'playas, empresas locales); si no lo hay, explica el efecto para los linenses sin inventar cifras locales.')

def adapt_topic(title):
    """Tema adaptable del titular (vivienda y precios, empleo y paro, economía…), o ''."""
    for name, rx in _ADAPT_TOPICS.items():
        if re.search(r'\b(' + rx + r')\b', title or '', re.I):
            return name
    return ''


def adaptable_source(meta):
    return str((meta or {}).get('name') or '').startswith(ADAPT_PREFIX)


# Ayudas o daños de una catástrofe en otro sitio (DANA de Valencia, volcán de La Palma…): no se adaptan
_ELSEWHERE_EVENT = re.compile(r'\b(dana|volc[aá]n|la\s+palma|paiporta|riadas?|damnificad\w*|zona\s+cero|reconstrucci[oó]n|'
                              r'afectados\s+por\s+(la|el)\s+\w+)\b', re.I)
_HOME_AREA = re.compile(r'andaluc|c[aá]diz|campo\s+de\s+gibraltar|l[ií]nea\s+de\s+la\s+concepci', re.I)


def adaptable_ok(title):
    """Titular nacional que se puede llevar a La Línea: tema que toca el bolsillo o el día a día, sin otra ciudad ni región."""
    title = title or ''
    if _ELSEWHERE_EVENT.search(title) and not _HOME_AREA.search(title):
        return False
    return bool(_ADAPT_TOPIC.search(title)) and not _ADAPT_NO.search(title) and not _OTHER_TOWNS.search(title) and not foreign(title)


ADAPT_DAILY_AI = 20  # tope de consultas de IA al día para adaptables (coste controlado)


def adapt_review(title, excerpt='', url=''):
    """Análisis estricto con IA de una noticia nacional: solo pasa si hay un vínculo CONCRETO y demostrable con La Línea.
    Devuelve la ficha {por_que, enfoque, titular, comprobar[], comprobado[{dato,fuente}], estado} o None si no se adapta
    (o si no hay IA: sin justificar, no se propone)."""
    from . import ai
    if not ai.AI_ENABLED:
        return None
    key = 'adapt_ai_' + datetime.now(timezone.utc).strftime('%Y-%m-%d')
    used = int(db.get_setting(key, '0') or 0)
    if used >= ADAPT_DAILY_AI:
        return None
    db.set_setting(key, str(used + 1))
    prompt = ('Eres el editor de InfoLinense (La Línea de la Concepción, Cádiz, junto a Gibraltar). Noticia NACIONAL: «%s». %s\nEnlace: %s\n'
              'Decide si se puede convertir en una noticia LOCAL con una conexión CONCRETA, DEMOSTRABLE y periodísticamente relevante con La Línea. '
              'Criterios válidos: norma nacional que afecta directamente a residentes o actividades de La Línea; ayudas aplicables al municipio; '
              'decisión estatal con consecuencias verificables para la ciudad; medida sobre Gibraltar con repercusión real en La Línea; datos nacionales '
              'que tienen estadística específica del municipio (paro registrado, renta, población, vivienda…); proyectos con relación comprobable con '
              'instituciones, empresas o vecinos de La Línea. NO valen relaciones genéricas («afecta a todos los españoles», «también en La Línea hay '
              'vivienda») ni especulativas. No inventes datos locales. Si puedes buscar en internet, busca el dato de La Línea y anótalo SOLO con su fuente '
              'real (URL). Devuelve JSON {"ok": true|false, "por_que": "vínculo concreto con La Línea", "enfoque": "planteamiento local", '
              '"titular": "posible titular centrado en La Línea", "comprobar": ["dato o documento local a verificar antes de publicar"], '
              '"comprobado": [{"dato": "…", "fuente": "URL"}]}. Si no hay conexión suficiente, ok=false.') % (title, (excerpt or '')[:900], url)
    try:
        data, _, _ = ai.ask_json(ai.SYSTEM, prompt, web=True, max_tokens=900)
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get('ok') or len(str(data.get('por_que') or '')) < 25:
        return None
    checked = [c for c in (data.get('comprobado') or []) if isinstance(c, dict) and str(c.get('fuente') or '').startswith('http') and c.get('dato')]
    todo = [str(x)[:200] for x in (data.get('comprobar') or []) if str(x).strip()][:6]
    return {'por_que': str(data.get('por_que'))[:500], 'enfoque': str(data.get('enfoque') or '')[:400], 'titular': str(data.get('titular') or '')[:160],
            'comprobar': todo, 'comprobado': [{'dato': str(c['dato'])[:250], 'fuente': str(c['fuente'])[:400]} for c in checked][:6],
            'estado': 'confirmada' if checked and not todo else 'pendiente'}


def adapt_angle(card):
    lines = [ADAPT_ANGLE, 'Por qué se adapta: ' + card['por_que']]
    if card.get('enfoque'):
        lines.append('Enfoque: ' + card['enfoque'])
    if card.get('titular'):
        lines.append('Titular propuesto: ' + card['titular'])
    if card.get('comprobado'):
        lines.append('Datos comprobados: ' + '; '.join('%s (%s)' % (c['dato'], c['fuente']) for c in card['comprobado']))
    if card.get('comprobar'):
        lines.append('ANTES DE PUBLICAR hay que comprobar: ' + '; '.join(card['comprobar']) + '. Sin comprobarlo, no lo des como hecho.')
    return ' '.join(lines)


def social_authorized(url, meta=None):
    """¿El enlace es de una cuenta de Facebook o Instagram que está en tu lista (y activa)?"""
    from . import social
    meta = meta or {}
    if meta.get('kind') == 'social' and meta.get('url') and social.same_account(url, meta['url']):
        return True
    return any(social.same_account(url, r['url']) for r in db.rows("SELECT url FROM sources WHERE kind='social' AND active=1"))


def lead_text(title, excerpt):
    """Lo que cuenta para saber de qué va una noticia: titular y comienzo. El resto del texto solo cuenta si nombra
    a La Línea más de una vez (una mención de pasada en una noticia de otra ciudad no vale)."""
    excerpt = excerpt or ''
    lead = (title or '') + ' ' + excerpt[:450]
    if len(_CITY_STRONG.findall(excerpt)) + len(re.findall(r'\bLa\s+L[ií]nea\b', excerpt)) >= 3:
        lead += ' ' + excerpt
    return lead


def local_lead(title, excerpt=''):
    """¿Es de La Línea por el contexto (titular o comienzo)?"""
    return bool(exact_locality(title or '') or exact_locality(lead_text(title, excerpt)))


_ctx = {'n': 0}
CTX_LIMIT = 25  # comprobaciones con IA por búsqueda


def ai_context_ok(title, excerpt):
    """Caso dudoso (La Línea no está en el titular): la IA lee el contexto. Sin IA o sin cupo, vale."""
    from . import ai
    if not ai.AI_ENABLED or _ctx['n'] >= CTX_LIMIT:
        return True
    _ctx['n'] += 1
    prompt = ('Noticia: «%s». %s\n¿Trata de La Línea de la Concepción (Cádiz, junto a Gibraltar) o le afecta de forma directa '
              '(sus vecinos, su Ayuntamiento, sus calles, empresas, clubes o servicios)? Responde NO si es de otra ciudad, región o país '
              'y La Línea solo sale de pasada o en una lista. Devuelve JSON {"ok": true|false}.') % (title, (excerpt or '')[:900])
    try:
        data, _, _ = ai.ask_json(ai.SYSTEM, prompt, max_tokens=60)
        return bool(data.get('ok')) if isinstance(data, dict) and 'ok' in data else True
    except Exception:
        return True


_CHANGE = re.compile(r'\b(adjudic\w*|aprueb\w*|aprobad\w*|licita\w*|firm\w*|inici\w*|comienz\w*|empiez\w*|termin\w*|finaliz\w*|'
                     r'inaugur\w*|abre|abren|cierra|cierran|aplaz\w*|suspend\w*|cancel\w*|anul\w*|rectific\w*|recurs\w*|sentencia\w*|'
                     r'conden\w*|absuel\w*|detenid\w*|fallec\w*|dimit\w*|dimisi[oó]n|ces[ae]\w*|nombr\w*|ampl[ií]\w*|prorrog\w*|'
                     r'nueva\s+fecha|retras\w*|denuncia\w*|multa\w*|rechaz\w*|desestim\w*|estima\w*|paraliz\w*|reanud\w*|'
                     r'ultim\w*|definitiv\w*|oficial\w*|confirm\w*|desmient\w*)\b', re.I)


def important_change(old_title, old_excerpt, title, excerpt):
    """¿La nueva versión trae algo importante que la vista no tenía? (un paso nuevo: se adjudica, se aprueba, se aplaza…,
    o cifras y fechas nuevas)."""
    old = (old_title or '') + ' ' + (old_excerpt or '')[:600]
    new_words = {m.group(0).lower()[:6] for m in _CHANGE.finditer(title or '')}
    old_words = {m.group(0).lower()[:6] for m in _CHANGE.finditer(old)}
    if new_words - old_words:
        return True
    nums = lambda t: {n for n in re.findall(r'\d[\d.,]*', t or '') if not re.fullmatch(r'20\d\d', n) and len(n) >= 2}
    return bool(nums(title) - nums(old))


def find_decided(title, excerpt='', days=30):
    """Noticia que ya pasó por tus manos (dijiste que sirve o que no, está en redacción o publicada) y cuenta lo mismo."""
    if _TENDER.search(title or ''):
        return None
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    for c in db.rows("""SELECT c.id,c.title,c.excerpt FROM candidates c WHERE c.created_at>=? AND c.status!='merged'
                        AND (COALESCE(c.editorial_priority,'undecided') NOT IN ('undecided','') OR c.status='published'
                             OR EXISTS (SELECT 1 FROM articles a WHERE a.candidate_id=c.id))
                        ORDER BY c.id DESC LIMIT 600""", (since,)):
        if story_match(title, excerpt, c['title'], c.get('excerpt')):
            return c
    return None


def add_candidate(title, url, excerpt, source_name, source_id=None, published_at='', source_meta=None, local_angle='', outlet='', image=''):
    source_meta = source_meta or {'priority': 60, 'official': 0, 'local_scope': 0}
    title, excerpt = clean(title)[:260], clean(excerpt)[:4000]
    local_angle = clean(local_angle)[:800]
    adapt_card = None
    if source_meta.get('kind') == 'social' and (source_meta.get('brand') or 'infolinense') == 'infolinense' \
            and other_place(title + ' ' + excerpt[:600]):
        return None  # publicación de redes sobre otro pueblo
    if re.search(r'(^|\.)(facebook|instagram)\.com/', re.sub(r'^https?://(www\.|m\.)?', '', url or '')) and not social_authorized(url, source_meta):
        return None  # Facebook e Instagram: solo las cuentas que tú has autorizado en Fuentes de redes sociales
    if adaptable_source(source_meta):
        if not adaptable_ok(title) or not recent_enough(published_at):
            return None
        if source_id and (db.row("SELECT COUNT(*) n FROM candidates WHERE source_id=? AND created_at>=datetime('now','-1 day')",
                                 (source_id,)) or {}).get('n', 0) >= 3:
            return None  # calidad antes que cantidad: pocas al día
        if url and (db.row('SELECT 1 FROM candidates WHERE url=?', (url,)) or db.row('SELECT 1 FROM candidate_links WHERE url=?', (url,))):
            return None
        if find_story(title, excerpt=excerpt) or find_decided(title, excerpt):
            return None  # la misma noticia nacional ya está (o ya la decidiste): no se gasta IA
        card = adapt_review(title, excerpt, url)
        if not card:
            return None  # sin conexión local concreta y demostrable: fuera
        adapt_card = card
        local_angle = adapt_angle(card)
        source_meta = dict(source_meta, local_scope=1)  # ya filtrada: no se le pide «La Línea» en el texto
    elif str(source_meta.get('name') or source_name or '').startswith('Medios nacionales') and not exact_locality(title + ' ' + excerpt[:300]):
        return None  # mención de pasada a La Línea en un medio nacional
    tender = source_meta.get('kind') in ('procurement', 'bop', 'edictos') or bool(re.search(r'licitaci|adjudica|edicto|contrataci', title, re.I))
    window = WINDOW_DAYS['Licitaciones y edictos'] if tender else None
    if not title or not url or (not (tender and _open_tender_deadline(title, excerpt)) and not recent_enough(published_at, window)): return None
    if not source_meta.get('local_scope'):
        if tender:
            if not tender_ok(title, excerpt): return None  # edictos y licitaciones de otros ayuntamientos, fuera
        elif not place_ok(lead_text(title, excerpt), outlet if re.search(r'gibraltar|\.gi\b|chronicle|gbc', outlet or '', re.I) else ''):
            angle = comarca_angle(url) if _COMARCA.search(title + ' ' + excerpt) else ''
            if not angle:
                return None  # comarcal sin nada de La Línea, o de otro municipio
            local_angle = ('ENFOQUE LA LÍNEA: es una noticia comarcal; el titular y la entradilla se centran en lo que toca a '
                           'La Línea y el resto va después en el texto. Dato de La Línea: ' + angle)
    if not source_meta.get('local_scope') and foreign(title + ' ' + excerpt[:600]):
        return None  # otro país (p. ej. Semana Santa de Latinoamérica)
    own_brand = (source_meta.get('brand') or 'infolinense') != 'infolinense' and source_meta.get('local_scope')
    if not own_brand and not (source_meta.get('local_scope') and source_meta.get('official')) and _OTHER_TOWNS.search(title) and not exact_locality(title) and not gibraltar_topic(title) and not local_angle.startswith('ENFOQUE'):
        return None  # titular de otro municipio (Algeciras, San Roque…) sin nada de La Línea
    if url and (db.row('SELECT 1 FROM candidates WHERE url=?', (url,)) or db.row('SELECT 1 FROM candidate_links WHERE url=?', (url,))):
        return None
    checked_ctx = source_meta.get('local_scope') or local_angle.startswith('ENFOQUE') or tender
    if not checked_ctx and not _CITY_STRONG.search(title):
        from . import gazetteer
        if gazetteer.places_in(title) and not exact_locality(title) and not gibraltar_topic(title):
            return None  # el titular es de otro sitio (Castelldefels, Lima…)
        if not exact_locality(title) and not gibraltar_topic(title) and not ai_context_ok(title, excerpt):
            return None  # La Línea solo sale de pasada
    if not (source_meta.get('official') and source_meta.get('local_scope')):
        from . import learn
        if learn.blocked(title, excerpt):
            return None  # sitio que descartas y la noticia no es de La Línea
    if similar_to_published(title) and not (source_meta.get('official') and source_meta.get('local_scope')): return None
    official_local = bool(source_meta.get('official') and source_meta.get('local_scope'))
    same = None if official_local else (find_tender(title) if tender else find_story(title, excerpt=excerpt))
    if same:  # la misma noticia desde otra fuente: se une a la que ya hay
        attach_link(same, title, url, excerpt, source_name, outlet, published_at)
        return None
    update_of = None
    decided = None if official_local or tender else find_decided(title, excerpt)
    if decided:  # ya la viste y decidiste: no vuelve, salvo que haya un cambio importante
        if not important_change(decided['title'], decided.get('excerpt'), title, excerpt):
            attach_link(decided['id'], title, url, excerpt, source_name, outlet, published_at)
            return None
        update_of = decided['id']
        local_angle = ('ACTUALIZACIÓN de una noticia que ya viste («%s»): cuenta solo lo nuevo. ' % clean(decided['title'])[:120]) + local_angle
    if not official_local and is_duplicate(title, url): return None
    score = heuristic_score(title, excerpt + ' ' + local_angle, source_meta)
    if tender:
        score = min(100, score + 30)  # licitaciones y edictos, prioridad alta
    relevance = 'low' if score < 50 else ('medium' if score < 70 else 'high')
    from . import brands
    brand = brands.valid(source_meta.get('brand') or brands.DEFAULT)
    if brand == brands.DEFAULT:
        brand = brands.classify(title + ' ' + excerpt[:600]) or brand  # cofradías → El Cofrade Linense; carnaval → Carnavalinense
    return db.exec_('''INSERT OR IGNORE INTO candidates(source_id,source_name,title,url,published_at,excerpt,score,relevance,status,local_angle,outlet,image_hint,brand,update_of,adapt_json)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (source_id, source_name, title, url, published_at, excerpt, score, relevance, 'new', local_angle[:2000], clean(outlet)[:120], image or '', brand, update_of,
                     json.dumps(adapt_card, ensure_ascii=False) if adapt_card else None))


def _archive_recovery_status(item, source):
    """Explain whether a current municipal record can re-enter the radar."""
    kind = (source.get('kind') or '').lower()
    source_url = (source.get('url') or '').lower()
    municipal_source = (
        kind == 'edictos' and 'sedeelectronica.lalinea.es/edictos/' in source_url
    ) or (
        kind == 'procurement' and source.get('local_scope') and (
            'contrataciondelestado.es/sindicacion/' in source_url
            or 'contratos.gobierto.es/adjudicadores/alcaldia-del-ayuntamiento-de-la-linea-de-la-concepcion' in source_url
        )
    )
    title = clean(item.get('title') or '')[:260]
    url = item.get('url') or ''
    published = item.get('published_at') or ''
    if not title or not url:
        return None, 'registro_incompleto'
    candidate = db.row("""SELECT c.id,c.editorial_priority FROM candidates c
                          WHERE c.status='archived'
                            AND (c.url=? OR EXISTS (SELECT 1 FROM candidate_links l WHERE l.candidate_id=c.id AND l.url=?)
                                 OR (?='edictos' AND c.source_id=? AND c.title=?
                                     AND substr(c.published_at,1,10)=substr(?,1,10)))""",
                    (url, url, kind, source.get('id'), title, published))
    if not candidate:
        return None, 'sin_archivo_coincidente'
    if not municipal_source:
        return candidate, 'fuente_no_municipal'
    if not recent_enough(published, WINDOW_DAYS['Licitaciones y edictos']):
        return candidate, 'fuera_de_ventana'
    if _OTHER_TOWNS.search(title) and not exact_locality(title) and not gibraltar_topic(title):
        return candidate, 'otro_municipio'
    # La petición actual es volver a mostrar las publicaciones oficiales de esta
    # semana. Se reabren una vez; al recuperarlas se limpia el descarte anterior.
    if candidate.get('editorial_priority') == 'no_interest' and not published_this_week(published):
        return candidate, 'descartado_explicito'
    return candidate, 'recuperable'


def _archived_official_candidate(item, source):
    candidate, reason = _archive_recovery_status(item, source)
    return candidate if reason == 'recuperable' else None


def _resurface_archived_official(item, source):
    """Restore matching current records from the municipality's official sources.

    A no-interest mark is cleared only for current-week official notices requested
    again, so the item re-enters once and can be dismissed normally afterward.
    """
    candidate = _archived_official_candidate(item, source)
    if not candidate:
        return None

    title = clean(item.get('title') or '')[:260]
    published = item.get('published_at') or ''
    excerpt = clean(item.get('excerpt') or '')[:4000]
    score = min(100, heuristic_score(title, excerpt, source) + 30)
    relevance = 'high' if score >= 70 else ('medium' if score >= 50 else 'low')
    db.exec_('''UPDATE candidates SET source_id=?,source_name=?,title=?,published_at=?,excerpt=?,
                status='new',editorial_priority=CASE WHEN editorial_priority='no_interest' THEN 'undecided' ELSE editorial_priority END,
                reason=NULL,score=MAX(score,?),relevance=?
                WHERE id=?''',
             (source.get('id'), source.get('name') or '', title, published, excerpt,
              score, relevance, candidate['id']))
    return candidate['id']

def read_source(source, remember_state=True):
    if 'sedeelectronica.lalinea.es/edictos' in (source.get('url') or ''):
        return parse_edictos(source)  # el tablón siempre con su lector (sesión + plan B), se haya añadido como se haya añadido
    if 'licitaciones.io/' in (source.get('url') or ''):
        return parse_licitacionesio(source)
    if 'contrataciondelestado.es' in (source.get('url') or '') and 'news.google' not in (source.get('url') or ''):
        return parse_placsp(source, remember_state=remember_state)
    if source['kind'] == 'rss':
        try:
            items = parse_rss(source)
        except ET.ParseError:
            return parse_html(source)  # se añadió como RSS pero es una página web normal
        url = source.get('url') or ''
        if items and source.get('official') and re.search(r'/feed/?$', url):
            # RSS de WordPress oficial (lalinea.es): solo da las 10 últimas; se leen también las páginas 2 y 3 para no perder ninguna
            seen = {i.get('url') for i in items}
            for page in (2, 3):
                try:
                    more = parse_rss(dict(source, url=url.rstrip('/') + '/?paged=%d' % page))
                except Exception:
                    break
                more = [i for i in more if i.get('url') not in seen]
                if not more:
                    break
                seen.update(i.get('url') for i in more)
                items += more
        return items or parse_html(source)
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
        from . import social_publish
        mode = lambda m: db.exec_('UPDATE sources SET access_mode=? WHERE id=?', (m, source.get('id')))
        if 'instagram.com' in host:  # Instagram: API oficial para cuentas profesionales
            try:
                direct = social_publish.ig_posts_for(source['url'])
            except social_publish.SocialError as exc:
                mode('no'); raise ValueError(str(exc))
            if direct is not None:
                mode('api'); return direct
        else:
            # 1) Si te han dado acceso a la página (o es tuya), se leen sus publicaciones directamente de Facebook (todas)
            try:
                direct = social_publish.page_posts_for(source['url'], source.get('name') or '')
            except Exception:
                direct = None
            if direct is not None:
                mode('api'); return direct
        mode('buscador')  # sin acceso por la API: lo que los buscadores tengan indexado (parcial)
        # 2) Sin acceso, Facebook no deja leer la página: se usa lo que los buscadores tienen indexado
        from . import social
        if account == 'profile.php':  # páginas sin nombre: se busca por el nombre de la fuente
            query = 'site:facebook.com "%s" "La Línea"' % re.sub(r'\s*\(.*?\)|\s*·.*', '', source.get('name') or '')
        else:
            query = f'site:{host}/{account}'
        items = []
        pid = (parse_qs(target.query).get('id') or [''])[0]
        for it in social.web_search(query, days=7, limit=15):
            if not re.search(r'facebook\.com|instagram\.com', it.get('url') or ''):
                continue
            if account == 'profile.php' and pid and pid not in (it.get('url') or '') \
                    and not strict_local((it.get('title') or '') + ' ' + (it.get('snippet') or '')):
                continue  # búsqueda por nombre: puede traer páginas de otros pueblos
            if account != 'profile.php' and not social.same_account(it.get('url') or '', source['url']):
                continue  # publicación de otra página (los buscadores a veces mezclan páginas parecidas)
            items.append({'title': it.get('title') or '', 'url': it['url'], 'excerpt': it.get('snippet') or it.get('title') or '',
                          'published_at': parse_es_date(it.get('snippet', '')) or parse_es_date(it.get('title', ''))})
        if not items:  # plan B: Google News
            feed = 'https://news.google.com/rss/search?q=' + quote(f'site:{host}/{account} when:7d') + '&hl=es&gl=ES&ceid=ES:es'
            try:
                items = parse_rss({'url': feed})
            except Exception:
                items = []
        return items
    raise ValueError('Tipo de fuente no soportado')


def add_by_url(url):
    """Pegar un enlace en el buscador: dice dónde está esa noticia en el panel y, si no está, la añade."""
    url = url.strip()
    row = db.row('SELECT id,title,status,reason,merged_into FROM candidates WHERE url=?', (url,))
    if row:
        if row['status'] in ('merged', 'archived'):
            why = 'estaba unida a otra tarjeta' if row['status'] == 'merged' else 'estaba retirada (%s)' % (row.get('reason') or 'antigua o descartada')
            db.exec_("UPDATE candidates SET status='new',merged_into=NULL,reason=NULL,editorial_priority='undecided' WHERE id=?", (row['id'],))
            return {'query': url, 'seen': 1, 'added': 1, 'candidate_ids': [row['id']], 'note': 'La noticia %s; ya está otra vez en Ordenar.' % why}
        return {'query': url, 'seen': 1, 'added': 0, 'candidate_ids': [],
                'note': 'Ya estaba en el panel (%s): «%s».' % ({'new': 'en Ordenar', 'review_ready': 'en Redacción o Revisar',
                                                               'published': 'publicada'}.get(row['status'], row['status']), row['title'][:90])}
    link = db.row('SELECT c.id,c.title FROM candidate_links l JOIN candidates c ON c.id=l.candidate_id WHERE l.url=?', (url,))
    if link:
        return {'query': url, 'seen': 1, 'added': 0, 'candidate_ids': [],
                'note': 'Está dentro de la tarjeta «%s» (la misma noticia contada por otro medio).' % link['title'][:90]}
    r = fetch(url, timeout=25); r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    meta = lambda *names: next((m.get('content') for n in names for m in soup.select('meta[property="%s"], meta[name="%s"]' % (n, n)) if m.get('content')), '')
    title = clean(meta('og:title') or (soup.title.get_text() if soup.title else ''))
    title = re.sub(r'\s+[-–|]\s+[^-–|]{3,40}$', '', title)
    excerpt = clean(meta('og:description', 'description'))
    published = meta('article:published_time', 'og:published_time') or page_date(url) or datetime.now(timezone.utc).isoformat()
    host = (urlparse(url).hostname or '').removeprefix('www.')
    src = db.row("SELECT * FROM sources WHERE url LIKE ? AND active=1 ORDER BY official DESC LIMIT 1", ('%' + host + '%',)) or {}
    cid = add_candidate(title, url, excerpt, src.get('name') or host, src.get('id'), published,
                        {'priority': 80, 'official': src.get('official') or 0, 'local_scope': 1, 'brand': src.get('brand') or 'infolinense'},
                        image=best_image(r.text, url))
    if cid:
        return {'query': url, 'seen': 1, 'added': 1, 'candidate_ids': [cid], 'note': 'Añadida a Ordenar: «%s».' % title[:90]}
    return {'query': url, 'seen': 1, 'added': 0, 'candidate_ids': [],
            'note': 'No se añadió: el panel cree que ya está publicada en InfoLinense o es demasiado antigua.'}


def search_news(query):
    """Busca noticias recientes por tema en Google News y añade solo las que pasan el filtro local."""
    if re.match(r'\s*https?://', query or ''):
        return add_by_url(query)
    query = re.sub(r'\s+', ' ', clean(query or '')).strip()[:120]
    if len(query) < 3:
        raise ValueError('Escribe al menos tres caracteres para buscar.')

    # Primero se busca con el municipio explícito. Si no llega ninguna noticia
    # nueva, se amplía la consulta y add_candidate aplica el filtro de localidad.
    variants = [f'{query} "La Línea de la Concepción" when:30d', f'{query} "La Línea" (Cádiz OR Gibraltar OR linense) when:30d']
    seen = 0
    errors = []
    for index, term in enumerate(variants):
        feed = 'https://news.google.com/rss/search?q=' + quote(term) + '&hl=es&gl=ES&ceid=ES:es'
        try:
            items = parse_rss({'url': feed})
            seen = max(seen, len(items))
        except Exception as exc:
            errors.append(str(exc))
            continue

        added = []
        for item in items[:60]:
            published_at = item.get('published_at') or ''
            published = publication_datetime(published_at)
            if not published or published > datetime.now(timezone.utc) + timedelta(hours=6):
                continue
            candidate_id = add_candidate(
                item.get('title', ''), item.get('url', ''), item.get('excerpt', ''),
                'Búsqueda por tema', None, published_at,
                {'priority': 75, 'official': False, 'local_scope': False, 'kind': 'rss'},
                outlet=item.get('outlet', ''), image=item.get('image', ''))
            if candidate_id:
                added.append(candidate_id)
        if added:
            return {'query': query, 'seen': seen, 'added': len(added),
                    'candidate_ids': added, 'fallback': bool(index)}

    if errors and not seen:
        raise RuntimeError('Google News no respondió: ' + errors[-1][:160])
    return {'query': query, 'seen': seen, 'added': 0, 'candidate_ids': [], 'fallback': True}

def source_health(sid, seen=0, added=0, error=''):
    db.exec_('''UPDATE sources SET last_checked_at=CURRENT_TIMESTAMP,
               last_success_at=CASE WHEN ?='' THEN CURRENT_TIMESTAMP ELSE last_success_at END,
               last_error=?,items_seen=?,items_added=? WHERE id=?''',
             (error, error[:500], seen, added, sid))

def probe_source(sid):
    source = db.row('SELECT * FROM sources WHERE id=?', (sid,))
    if not source: raise ValueError('Fuente no encontrada')
    try:
        # Una comprobación de Ajustes no debe consumir cambios de estado de PLACSP.
        items = read_source(source, remember_state=False)
        statuses = {}
        recoverable = 0
        recovery_reasons = {}
        for item in items:
            archived, reason = _archive_recovery_status(item, source)
            if archived:
                recovery_reasons[reason] = recovery_reasons.get(reason, 0) + 1
            match = db.row('SELECT id,status,editorial_priority FROM candidates WHERE url=?', (item.get('url') or '',))
            if not match:
                match = db.row('''SELECT c.id,c.status,c.editorial_priority FROM candidate_links l
                                   JOIN candidates c ON c.id=l.candidate_id WHERE l.url=?''', (item.get('url') or '',))
            if not match:
                if archived:
                    match = {'id': archived['id'], 'status': 'archived',
                             'editorial_priority': archived.get('editorial_priority')}
            if not match:
                continue
            status = match.get('status') or 'unknown'
            statuses[status] = statuses.get(status, 0) + 1
            if status == 'archived' and archived and reason == 'recuperable':
                recoverable += 1
        source_health(sid, len(items))
        return {'ok': True, 'items_seen': len(items), 'existing_statuses': statuses,
                'recoverable_archived': recoverable, 'recovery_reasons': recovery_reasons}
    except Exception as exc:
        source_health(sid, error=str(exc))
        return {'ok': False, 'items_seen': 0, 'error': str(exc)[:250]}

_scan_state = {'busy': False, 'phase': '', 'added': 0, 'checked': 0, 'total': 0,
               'started_at': 0, 'finished_at': 0, 'errors': []}


def is_priority(source):
    """Lo primero en cada búsqueda: Ayuntamiento, edictos, licitaciones y boletines."""
    url = (source.get('url') or '').lower()
    return (source.get('kind') in ('edictos', 'procurement', 'bop') or 'lalinea.es' in url or 'licitaciones.io' in url
            or 'contrataciondelestado' in url or 'gobierto' in url or 'boe.es/rss' in url)


def _read_batch(sources, added, errors):
    """Lee varias fuentes a la vez (red en paralelo) y guarda en este hilo."""
    if not sources:
        return
    with ThreadPoolExecutor(max_workers=16) as pool:
        jobs = {pool.submit(read_source, source): source for source in sources}
        for job in as_completed(jobs):
            source = jobs[job]
            try:
                items = job.result(); raw_count = len(items); count = 0
                resurfaced = set()
                if source.get('official') and source.get('local_scope') and source.get('kind') == 'rss':
                    for item in items:
                        row = db.row("""SELECT id,status FROM candidates WHERE url=? AND (status='merged'
                                         OR (status='archived' AND reason='Fuera de La Línea'))""", (item.get('url') or '',))
                        if row and recent_enough(item.get('published_at'), WINDOW_DAYS['Ayuntamiento']):
                            db.exec_("UPDATE candidates SET status='new',merged_into=NULL,reason=NULL WHERE id=?", (row['id'],))
                            added.append(row['id']); resurfaced.add(item.get('url') or '')
                            count += 1; _scan_state['added'] += 1
                for item in items:
                    cid = _resurface_archived_official(item, source)
                    if cid:
                        added.append(cid); resurfaced.add(item.get('url') or '')
                        count += 1; _scan_state['added'] += 1
                items = [i for i in items if (i.get('url') or '') not in resurfaced
                         and not db.row('SELECT 1 FROM candidates WHERE url=?', (i['url'],))
                         and not db.row('SELECT 1 FROM candidate_links WHERE url=?', (i['url'],))]
                # Solo noticias actuales: si la lista no trae fecha, se lee de la propia página.
                undated = [i for i in items if not i.get('published_at') and source['kind'] not in ('procurement', 'edictos')][:20]
                if undated:
                    with ThreadPoolExecutor(max_workers=12) as dates:
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
                    if cid: added.append(cid); count += 1; _scan_state['added'] += 1
                source_health(source['id'], raw_count, count)
            except Exception as exc:
                errors.append(f"{source['name']}: {str(exc)[:250]}")
                source_health(source['id'], error=str(exc))
            finally:
                _scan_state['checked'] = _scan_state.get('checked', 0) + 1


def scan_all():
    if not _scan_lock.acquire(blocking=False):
        return {'added': [], 'errors': [], 'busy': True}
    _scan_state.update(busy=True, phase='Ayuntamiento, edictos y licitaciones', added=0,
                       checked=0, total=0, started_at=time.time(), errors=[])
    try:
        added, errors = [], []
        _deep['n'] = 0; _ctx['n'] = 0
        active = db.rows('SELECT * FROM sources WHERE active=1 ORDER BY priority DESC')
        _scan_state['total'] = len(active)
        first = [s_ for s_ in active if is_priority(s_)]
        rest = [s_ for s_ in active if not is_priority(s_)]
        # 1) Lo del Ayuntamiento primero: sale en pantalla en segundos
        _read_batch(first, added, errors)
        auto_ayto(added)
        # 2) Prensa, Gibraltar, redes…
        _scan_state['phase'] = 'Prensa y demás fuentes'
        _read_batch(rest, added, errors)
        from . import ai
        if any(ai.web_enabled(p) for p in ai.provider_chain()):
            _scan_state['phase'] = 'Búsqueda web con IA'
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
                        added.append(cid); _scan_state['added'] += 1
                        scope = str(item.get('scope') or '').lower()
                        db.exec_('UPDATE candidates SET scope=? WHERE id=?', (scope[:20], cid))
            except Exception as exc: errors.append('Descubrimiento web IA: ' + str(exc)[:250])
        auto_ayto(added)
        merged = merge_duplicates()
        archive_off_topic()
        added = [a for a in added if (db.row('SELECT status FROM candidates WHERE id=?', (a,)) or {}).get('status') != 'merged']
        archived = archive_stale()
        after_new(added)
        db.log('scan', f'Escaneo: {len(added)} nuevas; {merged} unidas por repetidas; {archived} antiguas retiradas; {len(errors)} errores')
        _scan_state.update(added=len(added), errors=errors[:20])
        return {'added': added, 'errors': errors, 'sources_checked': len(active), 'busy': False}
    finally:
        _scan_state.update(busy=False, phase='', finished_at=time.time())
        _scan_lock.release()

def auto_ayto(ids):
    """Las noticias del Ayuntamiento de hoy (salen sobre la 13:00-14:30) se marcan solas para hoy: se publican casi todas.
    Se puede desactivar en Ajustes."""
    if db.get_setting('auto_ayto', '1') != '1':
        return 0
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo('Europe/Madrid')).date().isoformat()
    n = 0
    for cid in ids:
        c = db.row("""SELECT c.*,s.kind source_kind FROM candidates c LEFT JOIN sources s ON s.id=c.source_id WHERE c.id=?""", (cid,))
        if not c or c.get('editorial_priority') not in ('undecided', '') or (c.get('brand') or 'infolinense') != 'infolinense':
            continue
        date = publication_datetime(c.get('published_at'))
        if source_group(c) == 'Ayuntamiento' and date and date.astimezone(ZoneInfo('Europe/Madrid')).date().isoformat() == today:
            db.exec_("UPDATE candidates SET editorial_priority='today',plan_reason='Ayuntamiento de hoy: marcada sola' WHERE id=?", (cid,))
            try:
                from . import pipeline
                pipeline.queue_auto_write(cid, 'today')
            except Exception:
                pass
            n += 1
    return n


def after_new(ids):
    """Lo nuevo que sigue vivo tras los filtros: temas que sigues, agenda y avisos urgentes al móvil."""
    from . import agenda, alerts, topics
    alive = [i for i in ids or [] if (db.row('SELECT status FROM candidates WHERE id=?', (i,)) or {}).get('status') not in (None, 'archived', 'merged')]
    for cid in alive:
        from . import formats
        for fn in (topics.check, agenda.from_candidate, formats.apply_suggestion):
            try:
                fn(cid)
            except Exception as exc:
                db.log('after_new_error', str(exc)[:200])
    try:
        alerts.check_new(alive)
    except Exception as exc:
        db.log('alert_error', str(exc)[:200])


def archive_off_topic():
    """Retira del radar lo que no es de La Línea (ni de Gibraltar con interés) y nadie ha marcado."""
    n = 0
    for r in db.rows("""SELECT c.id,c.title,c.excerpt,c.url,c.source_name,c.local_angle,c.adapt_json,c.brand,s.local_scope,s.official,s.kind FROM candidates c LEFT JOIN sources s ON s.id=c.source_id
                        WHERE c.status IN ('new','researched','needs_config') AND c.editorial_priority IN ('undecided','')
                        AND c.social_type IS NULL"""):
        title, excerpt = r['title'] or '', r['excerpt'] or ''
        if re.search(r'(facebook|instagram)\.com/', r.get('url') or '') and not social_authorized(r['url']):
            db.exec_("UPDATE candidates SET status='archived',reason='Cuenta de redes no autorizada' WHERE id=?", (r['id'],)); n += 1
            continue
        if not (r.get('local_scope') and r.get('official')):
            from . import learn
            why = learn.blocked(title, excerpt)
            if why:
                db.exec_("UPDATE candidates SET status='archived',reason=? WHERE id=?", (why[:200], r['id'])); n += 1
                continue
        if r.get('kind') == 'social' and (r.get('brand') or 'infolinense') == 'infolinense' and other_place(title + ' ' + excerpt[:600]):
            db.exec_("UPDATE candidates SET status='archived',reason='Fuera de La Línea' WHERE id=?", (r['id'],)); n += 1
            continue
        if not r.get('local_scope') and foreign(title + ' ' + excerpt[:600]):
            db.exec_("UPDATE candidates SET status='archived',reason='Fuera de La Línea' WHERE id=?", (r['id'],)); n += 1
            continue
        if 'NACIONAL ADAPTADA' in str(r.get('local_angle') or '') and (not adaptable_ok(title) or not r.get('adapt_json')):
            db.exec_("UPDATE candidates SET status='archived',reason='No se adapta a La Línea' WHERE id=?", (r['id'],)); n += 1
            continue
        if str(r.get('local_angle') or '').startswith('ENFOQUE') or (r.get('brand') or 'infolinense') != 'infolinense':
            continue  # comarcal con dato de La Línea comprobado, o de Cofrade/Carnaval
        if r.get('local_scope') and r.get('official'):
            continue  # fuentes oficiales de La Línea (Ayuntamiento): todo lo suyo se queda
        tender = bool(_TENDER.search(title))
        bad = (_OTHER_TOWNS.search(title) and not exact_locality(title) and not gibraltar_topic(title))
        if not r.get('local_scope'):
            bad = bad or (not tender_ok(title, excerpt) if tender else not place_ok(lead_text(title, excerpt)))
        if bad:
            db.exec_("UPDATE candidates SET status='archived',reason='Fuera de La Línea' WHERE id=?", (r['id'],)); n += 1
    return n


def archive_stale():
    """Retira del radar lo que ya no es actual y nadie ha marcado como útil."""
    now = datetime.now(timezone.utc)
    n = 0
    for row in db.rows("""SELECT c.id,c.published_at,c.created_at,c.source_name,c.outlet,c.url,c.title,c.excerpt,c.scope,c.social_type,s.kind
                          FROM candidates c LEFT JOIN sources s ON s.id=c.source_id
                          WHERE c.status IN ('new','researched','needs_config') AND c.editorial_priority IN ('undecided','')"""):
        limit = now - timedelta(days=WINDOW_DAYS.get(source_group(row), MAX_CANDIDATE_AGE_DAYS))
        date = publication_datetime(row.get('published_at')) or publication_datetime((row.get('created_at') or '').replace(' ', 'T') + '+00:00')
        if date and date < limit and not _open_tender_deadline(row.get('title'), row.get('excerpt'), now):
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
        base_url, fragment = urldefrag(url or '')
        if 'sedeelectronica.lalinea.es' in base_url:
            r = sede_get(base_url)  # la sede exige sesión también para ver cada edicto
        else:
            r = fetch(base_url, timeout=30); r.raise_for_status()
        if 'pdf' in (r.headers.get('content-type') or '').lower() or r.content[:4] == b'%PDF':
            from io import BytesIO
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(r.content))
            # Boletín (BOP): solo las páginas del anuncio, no todo el boletín
            page = re.search(r'page=(\d+)', fragment or '')
            start = max(0, int(page.group(1)) - (1 if int(page.group(1)) > 0 else 0)) if page else 0
            text = clean(' '.join((pg.extract_text() or '') for pg in reader.pages[start:start + 4]))
            num = re.search(r'anuncio=(\d{5,6})', fragment or '')
            if num:
                code = num.group(1)[:-3] + '.' + num.group(1)[-3:]
                at = text.find(code)
                if at >= 0:
                    nxt = re.search(r'\b\d{2,3}\.\d{3}\s*\.?\s*-', text[at + len(code):])
                    text = text[at:at + len(code) + (nxt.start() if nxt else 8000)]
            return text[:18000]
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
