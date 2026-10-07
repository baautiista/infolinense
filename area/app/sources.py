"""Radar del Campo de Gibraltar: lee fuentes, se queda con lo comarcal y mide si es exclusiva.

- Fuentes (tabla sources): contratación (PLACSP, Gobierto), boletines (BOP, BOE, BOJA), edictos, ayuntamientos,
  Mancomunidad, Junta, Gobierno, puertos, agenda y noticias nacionales para adaptar.
- Prensa (tabla press): lo que ya han contado Diario Área y la competencia. Si Área ya lo ha publicado, no se propone
  (salvo que sea una actualización). Si ningún medio lo ha contado, se marca como EXCLUSIVA.
"""
import difflib
import html
import io
import json
import re
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

from . import db
from .config import MAX_CANDIDATE_AGE_DAYS, TENDER_WINDOW_DAYS, PLACSP_PAGES, AREA_FEED_PAGES

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
_scan_lock = threading.Lock()

BLOCKS = ['Exclusivas', 'Obras y urbanismo', 'Agenda y cultura', 'Instituciones', 'Nacionales adaptables', 'Efemérides']

# ---------------------------------------------------------------- municipios

TOWNS = ['Algeciras', 'La Línea', 'San Roque', 'Los Barrios', 'Tarifa', 'Jimena', 'Castellar', 'San Martín del Tesorillo']
_TOWN_RX = {
    'Algeciras': re.compile(r'\balgecir(as|eñ[oa]s?)\b|\bgetares\b|\bel\s+rinconcillo\b|\bpunta\s+europa\b', re.I),
    'La Línea': re.compile(r'l[ií]nea\s+de\s+la\s+concepci[oó]n|\blinens[ea]s?\b|\batunara\b|\bsanta\s+margarita\b(?=.*l[ií]nea)|\bjunquillos\b|\bel\s+zabal\b|\bzabal\s+(bajo|alto)\b|\bpoligono\s+de\s+la\s+atunara\b', re.I),
    'San Roque': re.compile(r'\bsanroqueñ[oa]s?\b|\bsotogrande\b|\bguadarranque\b|\bpuente\s+mayorga\b|\btaraguilla\b|\bguadiaro\b|'
                            r'\btorreguadiaro\b|\bmiraflores\b(?=.*san\s+roque)|\bestaci[oó]n\s+de\s+san\s+roque\b|\balcaidesa\b|\bcampamento\b(?=.*san\s+roque)', re.I),
    'Los Barrios': re.compile(r'\bbarreñ[oa]s?\b|\bpalmones\b|\bguadacorte\b|\bmonte\s+de\s+la\s+torre\b', re.I),
    'Tarifa': re.compile(r'\btarifeñ[oa]s?\b|\bfacinas\b|\btahivilla\b|\bbaelo\s+claudia\b|\bvaldevaqueros\b|\bpunta\s+paloma\b|\blos\s+lances\b', re.I),
    'Jimena': re.compile(r'jimena\s+de\s+la\s+frontera|\bjimenat[oa]s?\b|\bjimenense', re.I),
    'Castellar': re.compile(r'castellar\s+de\s+la\s+frontera|\balmoraima\b|\bcastellarense', re.I),
    'San Martín del Tesorillo': re.compile(r'san\s+mart[ií]n\s+del\s+tesorillo|\btesorillo\b', re.I),
}
# Con mayúscula y con contexto (son nombres que también significan otras cosas)
_AMBIG = {
    'La Línea': re.compile(r'\bLa\s+L[ií]nea\b'),
    'San Roque': re.compile(r'\bSan\s+Roque\b'),
    'Los Barrios': re.compile(r'\bLos\s+Barrios\b'),
    'Tarifa': re.compile(r'\bTarifa\b(?!\s+(de|del|el[eé]ctrica|regulada|plana|social|nocturna|reducida|[uú]nica|general|fija|variable|PVPC)\b)'),
    'Jimena': re.compile(r'\bJimena\b'),
    'Castellar': re.compile(r'\bCastellar\b(?!\s+del\s+Vall)'),
}
_PREP_OK = {'La Línea', 'Los Barrios', 'Tarifa', 'Jimena', 'Castellar'}
_COMARCA = re.compile(r'campo\s+de\s+gibraltar|bah[ií]a\s+de\s+algeciras|\bapba\b|autoridad\s+portuaria\s+de\s+la\s+bah[ií]a|\barcgisa\b|'
                      r'mancomunidad\s+de\s+municipios\s+del\s+campo|\bcomarca\s+del\s+campo|puerto\s+de\s+algeciras|hospital\s+punta\s+europa|'
                      r'estrecho\s+de\s+gibraltar', re.I)
_CONTEXT = re.compile(r'(c[aá]diz|gibraltar|algeciras|campo|bah[ií]a|comarca|frontera|verja|ayuntamiento|alcald|andaluc|estrecho|refiner[ií]a|'
                      r'puerto|playa|feria|vecinos|municipio)', re.I)
_UNRELATED = re.compile(r'\bl[ií]neas?\s+(?:\d+|[a-z]\d*\b|de\s+metro|del\s+metro|ferroviaria|de\s+tren|de\s+alta|el[eé]ctrica|a[eé]rea|editorial|'
                        r'de\s+autob[uú]s|de\s+salida|de\s+meta|de\s+cr[eé]dito|roja|telef[oó]nica|de\s+fuego|de\s+defensa|de\s+ayuda|'
                        r'de\s+flotaci[oó]n|del\s+horizonte|de\s+producci[oó]n|de\s+negocio|de\s+investigaci[oó]n|de\s+trabajo|'
                        r'argumental|sucesoria|de\s+banda|de\s+cercan[ií]as|de\s+costa|blanca|caliente|directa|de\s+fondo)', re.I)
_GIB = re.compile(r'\bgibraltar\b|\bpe[ñn][oó]n\b|\bllanit[oa]s?\b|gibraltare[ñn]', re.I)
_GIB_TOPIC = re.compile(r'(relleno|reclamation|\bobras?\b|construcci[oó]n|proyecto|frontera|verja|tratado|acuerdo|schengen|transfronteriz|border|treaty|'
                        r'aeropuerto|t[uú]nel|colas|trabajadores)', re.I)


def towns_in(text):
    """Municipios del Campo de Gibraltar que aparecen en un texto."""
    text = text or ''
    found = [t for t, rx in _TOWN_RX.items() if rx.search(text)]
    for t, rx in _AMBIG.items():
        if t in found or not rx.search(text):
            continue
        if t == 'La Línea' and _UNRELATED.search(text):
            continue
        prep = t in _PREP_OK and re.search(r'\b(de|en|a|desde|hacia)\s+' + rx.pattern.replace('\\b', '', 1), text)
        if prep or _CONTEXT.search(text) or _COMARCA.search(text) or found:
            found.append(t)
    return [t for t in TOWNS if t in found]


def comarca_ok(text):
    """¿Es del Campo de Gibraltar? Un municipio, la comarca, el puerto o Gibraltar con algo que nos afecta."""
    text = text or ''
    if towns_in(text) or _COMARCA.search(text):
        return True
    t = re.sub(r'campo\s+de\s+gibraltar|estrecho\s+de\s+gibraltar|bah[ií]a\s+de\s+(algeciras|gibraltar)', ' ', text, flags=re.I)
    return bool(_GIB.search(t) and _GIB_TOPIC.search(t))


# ---------------------------------------------------------------- etiquetas y puntuación

TAGS = [
    ('licitacion', re.compile(r'licitaci|licita\b|licitar|pliego|adjudica|formaliza|contrato\s+de\s+(obras?|servicios?|suministro)|expediente\s+de\s+contrataci|concurso\s+p[uú]blico', re.I)),
    ('edicto', re.compile(r'\bedicto|anuncio\s+de\s+informaci[oó]n\s+p[uú]blica|exposici[oó]n\s+p[uú]blica|informaci[oó]n\s+p[uú]blica|aprobaci[oó]n\s+(inicial|definitiva)', re.I)),
    ('presupuesto', re.compile(r'presupuest|modificaci[oó]n\s+de\s+cr[eé]dito|subvenci[oó]n|fondos\s+(europeos|feder|next)|inversi[oó]n|millones\s+de\s+euros|\bpleno\b|junta\s+de\s+gobierno', re.I)),
    ('obras', re.compile(r'\bobras?\b|urbanis|urbaniz|\bpgou\b|plan\s+general|reurbaniz|rehabilit|viviendas|construcci[oó]n|edificio|licencia|'
                         r'carril\s+bici|rotonda|aparcamiento|parking|paseo\s+mar[ií]timo|demolici|nave|hotel|plan\s+especial', re.I)),
    ('asi_sera', re.compile(r'as[ií]\s+(ser[aá]|quedar[aá]|cambiar[aá]|va\s+a\s+(ser|quedar|cambiar)|es)\b|c[oó]mo\s+(ser[aá]|quedar[aá])|render|'
                            r'proyecto\s+b[aá]sico|anteproyecto|nuevo\s+(acceso|parque|edificio|paseo|centro)', re.I)),
    ('agenda', re.compile(r'concierto|festival|programaci[oó]n|semana\s+cultural|\bferia\b|exposici[oó]n\s+(de|sobre)|teatro|actuaci[oó]n|cartel|gira|'
                          r'jornadas|certamen|muestra|ruta|fin\s+de\s+semana|navidad|carnaval|cabalgata|velada', re.I)),
]
_STAGE = re.compile(r'adjudica|formaliza|licita|aprueba(do)?\s+(inicial|definitiv)|inicio\s+de\s+(las\s+)?obras|comienzan|arrancan|finaliza|terminan|'
                    r'concluyen|inaugura|plazo|abre|cierra|recurso|anula|suspende|desierta|ampl[ií]a', re.I)


def tag_for(title, excerpt='', kind=''):
    if kind == 'efemeride':
        return 'efemeride'
    text = (title or '') + ' ' + (excerpt or '')[:500]
    for name, rx in TAGS:
        if rx.search(text):
            return name
    return ''


def money_value(text):
    """Mayor importe en euros que aparece en un texto (para puntuar)."""
    best = 0.0
    for m in re.finditer(r'(\d{1,3}(?:[.\s]\d{3})+(?:,\d+)?|\d+(?:,\d+)?)\s*(millones|mill\.|M)?\s*(?:de\s+)?(?:€|euros|EUR)', text or '', re.I):
        num = m.group(1).replace(' ', '').replace('.', '').replace(',', '.')
        try:
            v = float(num) * (1_000_000 if m.group(2) else 1)
        except ValueError:
            continue
        best = max(best, v)
    return best


def score_for(title, excerpt, source, tag, exclusive=False, covered=0):
    score = round(int(source.get('priority') or 60) * 0.35)
    score += {'licitacion': 30, 'edicto': 24, 'presupuesto': 22, 'obras': 20, 'asi_sera': 26, 'agenda': 12, 'efemeride': 14}.get(tag, 0)
    if source.get('official'):
        score += 8
    amount = money_value((title or '') + ' ' + (excerpt or ''))
    score += 18 if amount >= 1_000_000 else 10 if amount >= 200_000 else 4 if amount >= 30_000 else 0
    if exclusive:
        score += 18
    score -= min(15, 5 * covered)
    return max(0, min(100, score))


# ---------------------------------------------------------------- utilidades

def clean(value):
    value = html.unescape(value or '')
    if '<' in value:
        value = BeautifulSoup(value, 'html.parser').get_text(' ', strip=True)
    return re.sub(r'\s+', ' ', value).strip()


def fetch(url, timeout=25, **kw):
    return requests.get(url, timeout=(12, timeout), headers={
        'User-Agent': UA, 'Accept-Language': 'es-ES,es;q=0.9',
        'Accept': 'application/rss+xml,application/atom+xml,application/xml,text/html,*/*;q=0.5'}, **kw)


def best_image(html_text, base=''):
    if not html_text or '<img' not in html_text:
        return ''
    soup = BeautifulSoup(html_text, 'html.parser')
    for img in soup.find_all('img'):
        options = []
        for part in (img.get('srcset') or '').split(','):
            bits = part.strip().split()
            if bits:
                try:
                    options.append((int(bits[1].rstrip('w')) if len(bits) > 1 and bits[1].endswith('w') else 0, bits[0]))
                except ValueError:
                    options.append((0, bits[0]))
        src = img.get('data-src') or img.get('src') or ''
        if src:
            options.append((int(img.get('width') or 0) if str(img.get('width') or '').isdigit() else 0, src))
        options = [o for o in options if o[1] and not o[1].startswith('data:')]
        if options:
            url = urljoin(base, max(options)[1])
            if not re.search(r'(logo|icon|avatar|emoji|gravatar)', url, re.I):
                return url
    return ''


_MONTHS = {'ene': 1, 'feb': 2, 'mar': 3, 'abr': 4, 'may': 5, 'jun': 6, 'jul': 7, 'ago': 8, 'sep': 9, 'set': 9, 'oct': 10, 'nov': 11, 'dic': 12,
           'jan': 1, 'apr': 4, 'aug': 8, 'dec': 12}


def parse_es_date(text, now=None):
    now = now or datetime.now(timezone.utc)
    t = (text or '').lower()
    m = re.search(r'\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b', t)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), 9, tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
    m = re.search(r'\b(\d{1,2})\s+(?:de\s+)?(ene|feb|mar|abr|may|jun|jul|ago|sep|set|oct|nov|dic|jan|apr|aug|dec)[a-z]*\.?(?:\s+(?:de\s+)?(\d{4}))?', t)
    if m:
        year = int(m.group(3) or now.year)
        try:
            d = datetime(year, _MONTHS[m.group(2)], int(m.group(1)), 9, tzinfo=timezone.utc)
            if not m.group(3) and d > now + timedelta(days=1):
                d = d.replace(year=year - 1)
            return d.isoformat()
        except ValueError:
            pass
    return ''


def publication_datetime(value):
    if not value:
        return None
    try:
        date = parsedate_to_datetime(value)
    except (ValueError, TypeError, IndexError):
        try:
            date = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except ValueError:
            return None
    if not date.tzinfo:
        date = date.replace(tzinfo=timezone.utc)
    return date.astimezone(timezone.utc)


def iso(value):
    d = publication_datetime(value)
    return d.isoformat() if d else ''


def recent_enough(value, days=None):
    date = publication_datetime(value)
    if date is None:
        return False
    return date >= datetime.now(timezone.utc) - timedelta(days=days or MAX_CANDIDATE_AGE_DAYS)


def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else ''


_DATE_META = ('article:published_time', 'og:published_time', 'datePublished', 'pubdate', 'publish-date', 'DC.date.issued', 'dc.date')


def page_date(url):
    try:
        r = fetch(url, timeout=12)
        if not r.ok:
            return ''
        soup = BeautifulSoup(r.text[:400000], 'html.parser')
        for key in _DATE_META:
            tag = soup.find('meta', attrs={'property': key}) or soup.find('meta', attrs={'name': key}) or soup.find('meta', attrs={'itemprop': key})
            if tag and tag.get('content') and publication_datetime(tag['content'].strip()):
                return tag['content'].strip()
        m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', r.text)
        if m and publication_datetime(m.group(1)):
            return m.group(1)
        art = soup.find('article')
        t = art.find('time', attrs={'datetime': True}) if art else None
        if t and publication_datetime(t['datetime']):
            return t['datetime']
    except Exception:
        pass
    return ''


# ---------------------------------------------------------------- lectores

def parse_rss(source):
    r = fetch(source['url'])
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for it in root.findall('.//item')[:150]:
        title, link = clean(it.findtext('title')), clean(it.findtext('link'))
        outlet = clean(it.findtext('source'))
        if outlet and title.endswith(' - ' + outlet):
            title = title[:-(len(outlet) + 3)].strip()
        if not (title and link):
            continue
        raw = (it.findtext('{http://purl.org/rss/1.0/modules/content/}encoded') or '') + (it.findtext('description') or '')
        media = next((m for m in (it.find('{http://search.yahoo.com/mrss/}content'), it.find('{http://search.yahoo.com/mrss/}thumbnail'),
                                  it.find('enclosure')) if m is not None), None)
        image = media.attrib.get('url', '') if media is not None and 'image' in media.attrib.get('type', 'image') else ''
        full = clean(it.findtext('{http://purl.org/rss/1.0/modules/content/}encoded') or '')
        desc = clean(it.findtext('description'))
        items.append({'title': title, 'url': link, 'excerpt': full if len(full) > len(desc) else desc,
                      'published_at': clean(it.findtext('pubDate')) or clean(it.findtext('{http://purl.org/dc/elements/1.1/}date')),
                      'outlet': outlet, 'image': image or best_image(raw, link)})
    if not items:
        ns = {'a': 'http://www.w3.org/2005/Atom'}
        for it in root.findall('.//a:entry', ns)[:150]:
            link = next((x.attrib.get('href', '') for x in it.findall('a:link', ns) if x.attrib.get('rel', 'alternate') == 'alternate'), '')
            title = clean(it.findtext('a:title', default='', namespaces=ns))
            if title and link:
                items.append({'title': title, 'url': link,
                              'excerpt': clean(it.findtext('a:summary', default='', namespaces=ns) or it.findtext('a:content', default='', namespaces=ns)),
                              'published_at': clean(it.findtext('a:published', default='', namespaces=ns) or it.findtext('a:updated', default='', namespaces=ns))})
    return items


def parse_html(source):
    r = fetch(source['url'])
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    for node in soup.select('nav, footer, header, aside, script, style'):
        node.decompose()
    links = soup.select('article a[href], h2 a[href], h3 a[href]') or soup.select('main a[href]')
    items, seen = [], set()
    host = urlparse(source['url']).hostname
    for a in links:
        title = clean(a.get_text(' ', strip=True))
        if len(title) < 28 or len(title) > 300:
            continue
        link, _ = urldefrag(urljoin(source['url'], a.get('href', '')))
        if urlparse(link).scheme not in ('http', 'https') or urlparse(link).hostname != host or link in seen:
            continue
        seen.add(link)
        parent = a.find_parent(['article', 'li']) or a.parent
        text = clean(parent.get_text(' ', strip=True) if parent else title)[:900]
        items.append({'title': title, 'url': link, 'excerpt': text, 'published_at': parse_es_date(text)})
        if len(items) >= 80:
            break
    return items


def parse_bop(source):
    """Anuncios de los dos últimos BOP de Cádiz que son del Campo de Gibraltar."""
    r = fetch(source['url'])
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    bulletins = []
    for a in soup.select('a[href]'):
        link = urljoin(source['url'], a.get('href', ''))
        if '/boletin/Boletin-numero-' in link and link not in bulletins:
            bulletins.append(link)
        if len(bulletins) == 2:
            break
    if not bulletins:
        raise ValueError('No se encontraron boletines recientes')
    out = []
    for link in bulletins:
        page = fetch(link)
        page.raise_for_status()
        bsoup = BeautifulSoup(page.text, 'html.parser')
        head = ' '.join(x.get_text(' ', strip=True) for x in bsoup.select('title, h1, h2')[:4])
        bdate = parse_es_date(head) or parse_es_date(bsoup.get_text(' ', strip=True)[:1500])
        for a in bsoup.select('a[href]'):
            title = clean(a.get_text(' ', strip=True))
            match = re.match(r'^(\d{2,3}\.\d{3})\s*\.?\s*-', title)
            if not match or not comarca_ok(title):
                continue
            base, fragment = urldefrag(urljoin(link, a.get('href', '')))
            permalink = base + '#' + (fragment + '&' if fragment else '') + 'anuncio=' + match.group(1).replace('.', '')
            out.append({'title': title[:260], 'url': permalink, 'excerpt': title, 'published_at': bdate})
    return out


def _dmy(value):
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', value or '')
    if not m:
        return ''
    return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), 9, 0, tzinfo=timezone.utc).isoformat()


def parse_gobierto(source):
    """Licitaciones de un adjudicador en contratos.gobierto.es (fecha, importe y plazo)."""
    r = fetch(source['url'], timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    out, seen = [], set()
    now = datetime.now(timezone.utc)
    organism = re.sub(r'^Gobierto\s*·\s*', '', source.get('name') or '')
    for a in soup.find_all('a', href=re.compile(r'/licitaciones/\d+')):
        url = urljoin(source['url'], a['href'].split('?')[0])
        title = clean(a.get_text(' ', strip=True))
        if url in seen or len(title) < 12:
            continue
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
            published = min(dates).isoformat()
        closing = max(dates) if len(dates) > 1 else None
        if not published:
            continue
        if not (closing and closing >= now) and not recent_enough(published, TENDER_WINDOW_DAYS):
            continue
        amount = re.search(r'\d[\d.,]*\s*(?:M\s*)?€', text)
        seen.add(url)
        out.append({'title': 'Licitación: ' + title[:240], 'url': url, 'published_at': published, 'organism': organism,
                    'amount': amount.group(0) if amount else '', 'deadline': closing.date().isoformat() if closing else '',
                    'excerpt': ' · '.join(x for x in [organism, ('Importe: ' + amount.group(0)) if amount else '',
                                                      ('Plazo hasta ' + closing.strftime('%d/%m/%Y')) if closing else ''] if x)})
    if not out and 'licitaciones/' not in r.text:
        raise ValueError('Gobierto no mostró licitaciones (puede haber cambiado de formato)')
    return out


# --- Plataforma de Contratación del Sector Público (ATOM CODICE) ---

_PLACSP_STATUS = {'PRE': 'Anuncio previo', 'PUB': 'En plazo', 'EV': 'En evaluación', 'ADJ': 'Adjudicada', 'RES': 'Resuelta',
                  'ANUL': 'Anulada', 'CERR': 'Cerrada'}
_PLACSP_TYPE = {'1': 'Suministros', '2': 'Servicios', '3': 'Obras', '21': 'Gestión de servicios públicos', '22': 'Concesión de servicios',
                '31': 'Concesión de obras', '40': 'Colaboración público-privada', '7': 'Administrativo especial', '8': 'Privado', '50': 'Patrimonial'}
_DOC_TAGS = {'LegalDocumentReference': 'Pliego administrativo', 'TechnicalDocumentReference': 'Pliego técnico',
             'AdditionalDocumentReference': 'Documento adicional', 'GeneralDocument': 'Documento'}


def _find(el, *path):
    """Primer descendiente siguiendo nombres locales (sin preocuparse de los espacios de nombres CODICE)."""
    cur = [el]
    for name in path:
        nxt = []
        for c in cur:
            nxt += [x for x in c.iter() if _local(x.tag) == name and x is not c]
        if not nxt:
            return None
        cur = nxt
    return cur[0]


def _text(el, *path):
    node = _find(el, *path)
    return clean(node.text) if node is not None and node.text else ''


def placsp_entry(entry):
    """Datos útiles de una entrada del ATOM de la Plataforma de Contratación."""
    title = _text(entry, 'title')
    link = next((x.attrib.get('href', '') for x in entry if _local(x.tag) == 'link' and x.attrib.get('href')), '')
    updated = _text(entry, 'updated')
    status_el = _find(entry, 'ContractFolderStatus')
    if status_el is None:
        return None
    status = _text(status_el, 'ContractFolderStatusCode')
    party = _find(status_el, 'LocatedContractingParty')
    organism = _text(party, 'PartyName', 'Name') if party is not None else ''
    party_city = _text(party, 'CityName') if party is not None else ''
    project = _find(status_el, 'ProcurementProject')
    pname = _text(project, 'Name') if project is not None else ''
    ptype = _text(project, 'TypeCode') if project is not None else ''
    place_city = _text(project, 'RealizedLocation', 'CityName') if project is not None else ''
    place_area = _text(project, 'RealizedLocation', 'CountrySubentity') if project is not None else ''
    budget = ''
    if project is not None:
        for key in ('TaxExclusiveAmount', 'TotalAmount', 'EstimatedOverallContractAmount'):
            budget = _text(project, 'BudgetAmount', key)
            if budget:
                break
    deadline = _text(status_el, 'TenderSubmissionDeadlinePeriod', 'EndDate')
    winner = _text(status_el, 'TenderResult', 'WinningParty', 'PartyName', 'Name')
    awarded = _text(status_el, 'TenderResult', 'LegalMonetaryTotal', 'TaxExclusiveAmount') or _text(status_el, 'TenderResult', 'LegalMonetaryTotal', 'PayableAmount')
    docs, seen = [], set()
    for node in status_el.iter():
        kind = _DOC_TAGS.get(_local(node.tag))
        if not kind:
            continue
        uri = _text(node, 'URI')
        name = _text(node, 'ID') or _text(node, 'FileName') or kind
        if uri and uri not in seen:
            seen.add(uri)
            docs.append({'name': name[:160], 'kind': kind, 'url': uri})
    return {'title': pname or title, 'url': link, 'updated': updated, 'status': status, 'organism': organism,
            'party_city': party_city, 'place_city': place_city, 'place_area': place_area, 'budget': budget, 'type': ptype,
            'deadline': deadline, 'winner': winner, 'awarded': awarded, 'docs': docs[:20]}


def _euros(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ''
    return ('{:,.2f} €'.format(v)).replace(',', 'X').replace('.', ',').replace('X', '.')


def placsp_item(e):
    """Convierte un expediente en propuesta si es del Campo de Gibraltar."""
    where = ' '.join([e['organism'], e['party_city'], e['place_city']])
    if not (towns_in(where) or _COMARCA.search(where) or re.search(r'algeciras|l[ií]nea de la concepci|san roque|los barrios|tarifa|jimena|castellar|tesorillo',
                                                                    where, re.I) or comarca_ok(e['title'])):
        return None
    status = e['status'] or 'PUB'
    verb = {'ADJ': 'Adjudicación', 'RES': 'Contrato formalizado', 'PRE': 'Anuncio previo', 'ANUL': 'Anulada', 'EV': 'En evaluación'}.get(status, 'Licitación')
    amount = _euros(e['awarded'] if status in ('ADJ', 'RES') and e['awarded'] else e['budget'])
    bits = [e['organism'], _PLACSP_TYPE.get(e['type'], ''), ('Importe: ' + amount) if amount else '',
            ('Plazo hasta ' + e['deadline'][:10]) if e['deadline'] and status == 'PUB' else '',
            ('Adjudicataria: ' + e['winner']) if e['winner'] else '', ('Lugar: ' + e['place_city']) if e['place_city'] else '',
            'Estado: ' + _PLACSP_STATUS.get(status, status)]
    return {'title': '%s: %s' % (verb, e['title'][:240]), 'url': (e['url'] or '') + '#estado=' + status,
            'excerpt': ' · '.join(b for b in bits if b), 'published_at': e['updated'], 'organism': e['organism'],
            'amount': amount, 'deadline': e['deadline'][:10], 'docs': e['docs'], 'towns_hint': where}


def parse_placsp(source):
    """ATOM de la Plataforma de Contratación: se leen las páginas más recientes y se filtra el Campo de Gibraltar."""
    out, url, pages = [], source['url'], 0
    limit = datetime.now(timezone.utc) - timedelta(days=TENDER_WINDOW_DAYS)
    while url and pages < max(1, PLACSP_PAGES):
        r = fetch(url, timeout=90)
        r.raise_for_status()
        pages += 1
        next_url, oldest = '', None
        for _, el in ET.iterparse(io.BytesIO(r.content), events=('end',)):
            name = _local(el.tag)
            if name == 'link' and el.attrib.get('rel') == 'next':
                next_url = el.attrib.get('href', '')
            elif name == 'entry':
                e = placsp_entry(el)
                if e:
                    d = publication_datetime(e['updated'])
                    oldest = d if d and (oldest is None or d < oldest) else oldest
                    item = placsp_item(e)
                    if item:
                        out.append(item)
                el.clear()
        if oldest and oldest < limit:
            break
        url = next_url
    return out


# --- Tablón de edictos de La Línea (sede con sesión) ---

_EXPIRED = re.compile(r'sesi[oó]n ha expirado|vuelva a iniciar sesi[oó]n|espere un momento por favor', re.I)


def parse_edictos(source):
    s = requests.Session()
    s.headers.update({'User-Agent': UA, 'Accept-Language': 'es-ES,es;q=0.9'})
    root = '%s://%s/' % (urlparse(source['url']).scheme, urlparse(source['url']).netloc)
    page = ''
    for target in (root + 'edictos/publico?idOrgan=23', source['url']):
        try:
            r = s.get(target, timeout=30)
            if r.ok and not _EXPIRED.search(r.text[:20000]):
                page = r.text
                break
        except requests.RequestException:
            continue
    if not page:
        raise ValueError('La sede no dejó leer el tablón (pide sesión). Se reintenta en la próxima búsqueda.')
    soup = BeautifulSoup(page, 'html.parser')
    out, seen = [], set()
    for row in soup.select('tr'):
        cells = row.find_all('td')
        link = row.find('a', href=re.compile(r'codigo=|edicto', re.I))
        if len(cells) < 3 or not link:
            continue
        title = max((clean(c.get_text(' ', strip=True)) for c in cells), key=len)
        url = urljoin(source['url'], re.sub(r';jsessionid=[^?#]+', '', link['href'], flags=re.I))
        published = _dmy(row.get_text(' ', strip=True))
        if title and published and url not in seen and recent_enough(published, TENDER_WINDOW_DAYS):
            seen.add(url)
            out.append({'title': 'Edicto La Línea: ' + title[:240], 'url': url, 'excerpt': title, 'published_at': published,
                        'organism': 'Ayuntamiento de La Línea'})
    return out


def read_source(source):
    kind = source.get('kind') or 'rss'
    if kind == 'placsp':
        return parse_placsp(source)
    if kind == 'gobierto':
        return parse_gobierto(source)
    if kind == 'bop':
        return parse_bop(source)
    if kind == 'edictos':
        return parse_edictos(source)
    if kind == 'html':
        return parse_html(source)
    try:
        return parse_rss(source) or parse_html(source)
    except ET.ParseError:
        return parse_html(source)


# ---------------------------------------------------------------- prensa: Diario Área y competencia

def read_press(ps):
    if ps['role'] == 'area' and 'diarioarea.com/feed' in ps['url']:
        items = []
        for page in range(1, max(1, AREA_FEED_PAGES) + 1):
            try:
                items += parse_rss({'url': ps['url'] + ('' if page == 1 else '?paged=%s' % page)})
            except Exception:
                if page == 1:
                    raise
                break
        return items
    return parse_rss(ps)


def refresh_press():
    """Guarda los titulares recientes de Diario Área y de la competencia."""
    errors = []
    active = db.rows('SELECT * FROM press_sources WHERE active=1')
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {pool.submit(read_press, p): p for p in active}
        for job in as_completed(jobs):
            p = jobs[job]
            try:
                items = job.result()
                for it in items:
                    outlet = p['name'].split(' (')[0]
                    if p['role'] == 'competitor' and it.get('outlet'):
                        outlet = it['outlet']
                    if p['role'] == 'competitor' and 'diarioarea' in (it.get('url') or '') + outlet.lower().replace(' ', ''):
                        continue
                    db.exec_('INSERT OR IGNORE INTO press(role,outlet,title,url,excerpt,published_at) VALUES(?,?,?,?,?,?)',
                             (p['role'], outlet[:80], clean(it['title'])[:300], it['url'], clean(it.get('excerpt'))[:600], iso(it.get('published_at'))))
                db.exec_('UPDATE press_sources SET last_checked_at=CURRENT_TIMESTAMP,last_error=NULL,items_seen=? WHERE id=?', (len(items), p['id']))
            except Exception as exc:
                errors.append('%s: %s' % (p['name'], str(exc)[:200]))
                db.exec_('UPDATE press_sources SET last_checked_at=CURRENT_TIMESTAMP,last_error=? WHERE id=?', (str(exc)[:400], p['id']))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    db.exec_("DELETE FROM press WHERE published_at!='' AND published_at<?", (cutoff,))
    return errors


# ---------------------------------------------------------------- misma historia

_STOP = set('''para como pero desde hasta sobre entre tras ante bajo contra durante mediante segun este esta estos estas ese esos esas aquel
todo toda todos todas otro otra otros otras nuevo nueva nuevos nuevas donde cuando quien cual tambien muy mas menos han hay ser sido sera
fue son estan tiene tienen hace hacen campo gibraltar algeciras linea concepcion roque barrios tarifa jimena castellar frontera ayuntamiento
municipal ciudad vecinos comarca comarcal edicto licitacion adjudicacion contrato servicio servicios obras estado plazo importe noticia hoy ayer
manana junta andalucia gobierno euros millones'''.split())
_TENDER = re.compile(r'^(licitaci|adjudicaci|contrato formalizado|anuncio previo|anulada|en evaluaci|edicto)', re.I)


def _fold(t):
    t = unicodedata.normalize('NFD', (t or '').lower())
    return ''.join(c for c in t if unicodedata.category(c) != 'Mn')


def story_tokens(title):
    t = re.sub(r'\s+[-–|]\s+[^-–|]{3,40}$', '', title or '')
    t = re.sub(r'^[^:]{3,40}:\s+', '', t) if _TENDER.search(t) else t
    out = set()
    for w in re.findall(r'[a-z0-9ñ]+', _fold(t)):
        if w.isdigit():
            if not re.fullmatch(r'20[12]\d', w):
                out.add(w)
        elif len(w) >= 4 and w not in _STOP:
            out.add(w[:6])
    return out


def same_story(a, b, loose=False):
    ta, tb = story_tokens(a), story_tokens(b)
    if min(len(ta), len(tb)) < 3:
        return False
    shared = len(ta & tb)
    need = 0.5 if loose else 0.6
    if shared >= 3 and shared / min(len(ta), len(tb)) >= need:
        return True
    return difflib.SequenceMatcher(None, ' '.join(sorted(ta)), ' '.join(sorted(tb))).ratio() >= 0.85


def coverage(title, excerpt='', published_at='', days=21):
    """¿Quién lo ha contado ya? → {'area': {...}|None, 'competitors': [{outlet,title,url}], 'update': bool}."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    loose = bool(_TENDER.search(title or ''))
    area, comp = None, []
    for p in db.rows("SELECT * FROM press WHERE published_at='' OR published_at>=? ORDER BY published_at DESC", (since,)):
        if not same_story(title, p['title'], loose):
            continue
        if p['role'] == 'area':
            area = area or p
        elif not any(c['outlet'] == p['outlet'] for c in comp):
            comp.append({'outlet': p['outlet'], 'title': p['title'], 'url': p['url'], 'published_at': p['published_at']})
    update = False
    if area:
        mine, theirs = publication_datetime(published_at), publication_datetime(area.get('published_at'))
        newer = bool(mine and theirs and mine - theirs > timedelta(hours=20))
        stage_mine = set(m.group(0).lower()[:6] for m in _STAGE.finditer(title + ' ' + (excerpt or '')[:300]))
        stage_area = set(m.group(0).lower()[:6] for m in _STAGE.finditer(area['title']))
        nums_mine = set(re.findall(r'\d[\d.,]{2,}', title)) - set(re.findall(r'\d[\d.,]{2,}', area['title']))
        update = newer and bool((stage_mine - stage_area) or nums_mine)
    return {'area': area, 'competitors': comp[:6], 'update': update}


def apply_coverage(cid):
    c = db.row('SELECT * FROM candidates WHERE id=?', (cid,))
    if not c:
        return None
    cov = coverage(c['title'], c.get('excerpt') or '', c.get('published_at') or '')
    area_state = ('update' if cov['update'] else 'published') if cov['area'] else ''
    exclusive = 1 if (not cov['competitors'] and not cov['area'] and c.get('block') != 'Nacionales adaptables'
                      and c.get('tag') != 'efemeride') else 0
    src = db.row('SELECT * FROM sources WHERE id=?', (c['source_id'],)) if c.get('source_id') else None
    score = score_for(c['title'], c.get('excerpt') or '', src or {'priority': 60}, c.get('tag') or '', bool(exclusive), len(cov['competitors']))
    status = c['status']
    if area_state == 'published' and status == 'new' and c['priority'] in ('undecided', ''):
        status = 'in_area'
    elif area_state != 'published' and status == 'in_area':
        status = 'new'
    db.exec_('''UPDATE candidates SET area_state=?,area_url=?,area_title=?,exclusive=?,competitors_json=?,score=?,status=? WHERE id=?''',
             (area_state, (cov['area'] or {}).get('url', ''), (cov['area'] or {}).get('title', ''), exclusive,
              json.dumps(cov['competitors'], ensure_ascii=False), score, status, cid))
    return cov


def find_story(title, exclude=None, days=5):
    if _TENDER.search(title or ''):
        return None
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    for c in db.rows("SELECT id,title FROM candidates WHERE status NOT IN ('archived','merged') AND created_at>=? ORDER BY id", (since,)):
        if c['id'] != exclude and not _TENDER.search(c['title'] or '') and same_story(title, c['title']):
            return c['id']
    return None


def attach_link(cid, item, source_name):
    db.exec_('INSERT OR IGNORE INTO candidate_links(candidate_id,source_name,outlet,url,title,excerpt,published_at) VALUES(?,?,?,?,?,?,?)',
             (cid, source_name, clean(item.get('outlet'))[:120], item['url'], clean(item['title'])[:260], clean(item.get('excerpt'))[:2000],
              iso(item.get('published_at'))))


def links_for(ids):
    if not ids:
        return {}
    out = {}
    for r in db.rows('SELECT * FROM candidate_links WHERE candidate_id IN (%s) ORDER BY id' % ','.join('?' * len(ids)), tuple(ids)):
        out.setdefault(r['candidate_id'], []).append({k: r[k] for k in ('source_name', 'outlet', 'url', 'title', 'published_at')})
    return out


# ---------------------------------------------------------------- alta de propuestas

def add_candidate(item, source, force=False):
    """Crea una propuesta si es nueva, del Campo de Gibraltar y actual. Devuelve su id o None."""
    title, url = clean(item.get('title'))[:300], (item.get('url') or '').strip()
    excerpt = clean(item.get('excerpt'))[:4000]
    if not title or not url:
        return None
    kind = source.get('kind') or ''
    tender = kind in ('placsp', 'gobierto', 'bop', 'edictos') or bool(_TENDER.search(title))
    published = iso(item.get('published_at'))
    if not force and not recent_enough(published, TENDER_WINDOW_DAYS if tender else None):
        return None
    if publication_datetime(published) and publication_datetime(published) > datetime.now(timezone.utc) + timedelta(hours=6):
        return None
    block = source.get('block') or 'Instituciones'
    hay = ' '.join([title, excerpt[:1500], item.get('towns_hint') or '', item.get('organism') or ''])
    towns = towns_in(hay)
    if block != 'Nacionales adaptables' and not force and not comarca_ok(hay):
        return None
    if db.row('SELECT 1 FROM candidates WHERE url=?', (url,)) or db.row('SELECT 1 FROM candidate_links WHERE url=?', (url,)):
        return None
    same = find_story(title)
    if same:
        attach_link(same, item, source.get('name') or '')
        return None
    tag = tag_for(title, excerpt, kind)
    if block == 'Instituciones' and tag in ('licitacion', 'edicto', 'presupuesto'):
        block = 'Exclusivas'
    elif block == 'Instituciones' and tag in ('obras', 'asi_sera'):
        block = 'Obras y urbanismo'
    elif block == 'Instituciones' and tag == 'agenda':
        block = 'Agenda y cultura'
    if not towns and block == 'Nacionales adaptables':
        towns = []
    cid = db.exec_('''INSERT OR IGNORE INTO candidates(source_id,source_name,outlet,title,url,published_at,excerpt,image_hint,block,tag,towns,
                      organism,amount,deadline,docs_json,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'new')''',
                   (source.get('id'), source.get('name'), clean(item.get('outlet'))[:120], title, url, published, excerpt,
                    item.get('image') or '', block, tag, ','.join(towns), clean(item.get('organism'))[:200], item.get('amount') or '',
                    item.get('deadline') or '', json.dumps(item.get('docs') or [], ensure_ascii=False)))
    if cid:
        apply_coverage(cid)
    return cid


def source_health(sid, seen=0, added=0, error=''):
    db.exec_('''UPDATE sources SET last_checked_at=CURRENT_TIMESTAMP,
               last_success_at=CASE WHEN ?='' THEN CURRENT_TIMESTAMP ELSE last_success_at END,
               last_error=?,items_seen=?,items_added=? WHERE id=?''', (error, error[:500], seen, added, sid))


def probe_source(sid):
    source = db.row('SELECT * FROM sources WHERE id=?', (sid,))
    if not source:
        raise ValueError('Fuente no encontrada')
    try:
        items = read_source(source)
        source_health(sid, len(items))
        return {'ok': True, 'items_seen': len(items), 'sample': [i['title'] for i in items[:5]]}
    except Exception as exc:
        source_health(sid, error=str(exc))
        return {'ok': False, 'items_seen': 0, 'error': str(exc)[:250]}


def _with_dates(items, kind):
    undated = [i for i in items if not publication_datetime(i.get('published_at'))
               and not db.row('SELECT 1 FROM candidates WHERE url=?', (i['url'],))][:30]
    if undated and kind in ('rss', 'html'):
        with ThreadPoolExecutor(max_workers=8) as pool:
            for item, date in zip(undated, pool.map(lambda i: page_date(i['url']), undated)):
                item['published_at'] = date
    return items


def scan_all():
    if not _scan_lock.acquire(blocking=False):
        return {'added': [], 'errors': [], 'busy': True}
    try:
        errors = refresh_press()
        added = []
        active = db.rows('SELECT * FROM sources WHERE active=1 ORDER BY priority DESC')
        with ThreadPoolExecutor(max_workers=6) as pool:
            jobs = {pool.submit(lambda s: _with_dates(read_source(s), s.get('kind')), s): s for s in active}
            for job in as_completed(jobs):
                source = jobs[job]
                try:
                    items, count = job.result(), 0
                    for item in items:
                        cid = add_candidate(item, source)
                        if cid:
                            added.append(cid)
                            count += 1
                    source_health(source['id'], len(items), count)
                except Exception as exc:
                    errors.append('%s: %s' % (source['name'], str(exc)[:250]))
                    source_health(source['id'], error=str(exc))
        from . import efemerides, ai
        added += efemerides.as_candidates()
        if any(ai.web_enabled(p) for p in ai.provider_chain()):
            try:
                from . import redaccion
                for item in redaccion.discover():
                    cid = add_candidate(item, {'id': None, 'name': 'Búsqueda web IA', 'block': item.get('block') or 'Exclusivas',
                                               'priority': 80, 'official': 1 if item.get('official') else 0, 'kind': 'ai'})
                    if cid:
                        added.append(cid)
            except Exception as exc:
                errors.append('Búsqueda web IA: ' + str(exc)[:200])
        recheck_open()
        archived = archive_stale()
        db.log('scan', 'Búsqueda: %s nuevas, %s retiradas por antiguas, %s errores' % (len(added), archived, len(errors)))
        return {'added': added, 'errors': errors, 'sources_checked': len(active), 'busy': False}
    finally:
        _scan_lock.release()


def recheck_open(days=10):
    """Lo que Área publica después también retira (o marca como actualización) lo que ya estaba en el radar."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    for c in db.rows("SELECT id FROM candidates WHERE status IN ('new','in_area','chosen') AND created_at>=?", (since,)):
        apply_coverage(c['id'])


def archive_stale():
    now = datetime.now(timezone.utc)
    n = 0
    for r in db.rows("""SELECT id,published_at,created_at,tag,block,deadline FROM candidates
                        WHERE status IN ('new','in_area') AND priority IN ('undecided','')"""):
        days = TENDER_WINDOW_DAYS if r['block'] == 'Exclusivas' else MAX_CANDIDATE_AGE_DAYS + 1
        if r['tag'] == 'efemeride':
            days = 11
        date = publication_datetime(r['published_at']) or publication_datetime((r['created_at'] or '').replace(' ', 'T') + '+00:00')
        open_deadline = r.get('deadline') and r['deadline'] >= now.date().isoformat()
        if date and date < now - timedelta(days=days) and not open_deadline:
            db.exec_("UPDATE candidates SET status='archived' WHERE id=?", (r['id'],))
            n += 1
    return n


# ---------------------------------------------------------------- texto completo

_BOILER = re.compile(r'(cookies?|suscr[ií]bete|newsletter|todos los derechos|aviso legal|pol[ií]tica de privacidad|comparte|compartir|'
                     r'te puede interesar|lee tambi[eé]n|m[aá]s noticias|publicidad|s[ií]guenos|whatsapp|telegram)', re.I)


def fetch_article_text(url):
    """Texto de la noticia o del PDF del anuncio, sin menús."""
    if not url or url.startswith('efemeride://'):
        return ''
    try:
        if 'news.google.com' in url:
            from .photos import resolve_google_news
            url = resolve_google_news(url)
        base_url, fragment = urldefrag(url)
        r = fetch(base_url, timeout=40)
        r.raise_for_status()
        if 'pdf' in (r.headers.get('content-type') or '').lower() or r.content[:4] == b'%PDF':
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(r.content))
            text = clean(' '.join((pg.extract_text() or '') for pg in reader.pages[:6]))
            num = re.search(r'anuncio=(\d{5,6})', fragment or '')
            if num:
                code = num.group(1)[:-3] + '.' + num.group(1)[-3:]
                at = text.find(code)
                if at >= 0:
                    nxt = re.search(r'\b\d{2,3}\.\d{3}\s*\.?\s*-', text[at + len(code):])
                    text = text[at:at + len(code) + (nxt.start() if nxt else 8000)]
            return text[:18000]
        soup = BeautifulSoup(r.text, 'html.parser')
        for node in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'form', 'noscript']):
            node.decompose()
        best, best_len = None, 0
        for box in soup.select('article, main, [itemprop=articleBody], .entry-content, .post-content, .article-body, .noticia, .content, body'):
            paras = [clean(p.get_text(' ', strip=True)) for p in box.find_all(['p', 'li', 'td'])]
            size = sum(len(p) for p in paras if len(p) > 60)
            if size > best_len:
                best, best_len = paras, size
        if best and best_len > 300:
            body = [p for p in best if len(p) > 40 and not _BOILER.search(p[:80])]
            return '\n\n'.join(dict.fromkeys(body))[:18000]
        main = soup.find('article') or soup.find('main') or soup.body
        return clean(main.get_text(' ', strip=True) if main else soup.get_text(' ', strip=True))[:18000]
    except Exception:
        return ''
