"""Búsqueda de fotos para la noticia.

Orden: foto de la propia noticia (og:image) y búsqueda en Google Imágenes, como la haría una persona.
Si Google no responde se usan DuckDuckGo y Bing como reserva.
"""
import hashlib
import json
import re
from io import BytesIO
from urllib.parse import quote, quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image

from .config import UPLOAD_DIR, GOOGLE_SEARCH_API_KEY, GOOGLE_SEARCH_CX
from . import db, sources

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')
HEADERS = {'User-Agent': UA, 'Accept-Language': 'es-ES,es;q=0.9'}
_JUNK = re.compile(r'(logo|icon|avatar|sprite|placeholder|favicon|blank|pixel|1x1|spacer|badge|banner-|default-share|'
                   r'share-default|no-image|noimage|gravatar|emoji|\.svg|\.gif)(\b|$|[?._-])', re.I)
_STOP = {'para', 'como', 'sobre', 'desde', 'hasta', 'entre', 'tras', 'ante', 'linea', 'línea', 'concepcion', 'concepción',
         'ayuntamiento', 'municipal', 'linense', 'linenses', 'según', 'segun', 'este', 'esta', 'estos', 'estas', 'tiene',
         'será', 'sera', 'han', 'más', 'mas', 'europa', 'diario', 'área', 'area'}


def _host(url):
    return (urlparse(url or '').hostname or '').replace('www.', '')


def _item(url, page, kind):
    return {'url': url, 'source': page or '', 'source_name': _host(page) or _host(url), 'license': '',
            'author': '', 'kind': kind, 'publish_safe': True}


def keywords(title, n=6):
    words = [w for w in re.findall(r'[A-Za-zÁÉÍÓÚÑÜáéíóúñü0-9]{4,}', title or '') if w.lower() not in _STOP]
    return words[:n]


def resolve_google_news(url):
    """Google News enlaza a una página intermedia; devuelve la URL original de la noticia."""
    if 'news.google.com' not in (url or ''):
        return url
    try:
        page = requests.get(url, headers=HEADERS, timeout=15)
        if 'news.google.com' not in page.url:
            return page.url
        soup = BeautifulSoup(page.text, 'html.parser')
        direct = soup.select_one('[data-n-au]')
        if direct and direct.get('data-n-au', '').startswith('http'):
            return direct['data-n-au']
        node = soup.select_one('c-wiz[data-p]') or soup.select_one('[data-n-a-sg]')
        sg, ts = node.get('data-n-a-sg') if node else None, node.get('data-n-a-ts') if node else None
        gid = urlparse(url).path.rstrip('/').split('/')[-1]
        if not (sg and ts and gid):
            return url
        inner = ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
                 '"X","X",1,[1,1,1],1,1,null,0,0,null,0],"%s",%s,"%s"]') % (gid, ts, sg)
        payload = 'f.req=' + quote(json.dumps([[['Fbv4je', inner, None, 'generic']]]))
        r = requests.post('https://news.google.com/_/DotsSplashUi/data/batchexecute', data=payload, timeout=15,
                          headers={**HEADERS, 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'})
        m = re.search(r'garturlres\\",\\"(https?:[^\\"]+)', r.text)
        return m.group(1).encode().decode('unicode_escape') if m else url
    except Exception:
        return url


def page_images(url, limit=6):
    """Fotos destacadas de una página: og:image, twitter:image y las del cuerpo del artículo."""
    out = []
    if not url:
        return out
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        base = r.url
        found = []
        for attrs in ({'property': 'og:image'}, {'property': 'og:image:url'}, {'name': 'twitter:image'}, {'name': 'twitter:image:src'}):
            tag = soup.find('meta', attrs=attrs)
            if tag and tag.get('content'):
                found.append(urljoin(base, tag['content']))
        for img in soup.select('article img, main img, .article img, .entry-content img, .post-content img, figure img')[:15]:
            src = sources.best_image(str(img), base)
            if src:
                found.append(full_size(src))
        for u in found:
            if u.startswith('http') and not _JUNK.search(u):
                out.append(_item(u, base, 'source'))
    except Exception:
        pass
    return _unique(out)[:limit]


def same_story_images(title, exclude_url='', limit=6):
    """Fotos de otros medios que publican la misma noticia (búsqueda en Google News)."""
    words = keywords(title, 7)
    if len(words) < 2:
        return []
    query = ' '.join(words) + ' "La Línea"'
    feed = 'https://news.google.com/rss/search?q=' + quote(query) + '&hl=es&gl=ES&ceid=ES:es'
    out = []
    try:
        r = requests.get(feed, headers=HEADERS, timeout=15)
        links = re.findall(r'<link>(https://news\.google\.com/rss/articles/[^<]+)</link>', r.text)[:4]
        for link in links:
            real = resolve_google_news(link)
            if real == exclude_url or 'news.google.com' in real:
                continue
            out += [dict(x, kind='other_outlet') for x in page_images(real, limit=2)]
            if len(out) >= limit:
                break
    except Exception:
        pass
    return out[:limit]


_GOOGLE_COOKIES = {'CONSENT': 'YES+cb.20240101-00-p0.es+FX+000',
                   'SOCS': 'CAESHAgBEhJnd3NfMjAyNDAxMDEtMF9SQzIaAmVzIAEaBgiA_LqsBg'}
# Navegador antiguo: Google devuelve la versión sencilla (sin JavaScript) de Google Imágenes.
_BASIC_UA = 'Mozilla/5.0 (Windows NT 6.1; WOW64; rv:40.0) Gecko/20100101 Firefox/40.1'
# Bancos de imágenes con marca de agua y webs cuyas fotos no se pueden descargar.
_BAD_HOSTS = ('shutterstock.', 'istockphoto.', 'gettyimages.', 'alamy.', 'dreamstime.', '123rf.', 'depositphotos.',
              'freepik.', 'adobe.com', 'stock.adobe', 'pinterest.', 'pinimg.', 'fbsbx.', 'facebook.', 'instagram.',
              'tiktok.', 'twitter.', 'twimg.', 'lookaside.', 'youtube.', 'ytimg.')


def _bad_host(url):
    h = _host(url)
    return h == 'x.com' or any(b in h for b in _BAD_HOSTS)


def _google_full_html(text, limit):
    """Versión completa de Google Imágenes: cada resultado trae ["URL original", alto, ancho]."""
    out = []
    text = text.replace('\\u003d', '=').replace('\\u0026', '&').replace('\\/', '/')
    for m in re.finditer(r'\["(https?://[^"\]]+?)",(\d{2,5}),(\d{2,5})\]', text):
        u = m.group(1)
        if 'gstatic.com' in u or 'google.' in _host(u) or _JUNK.search(u) or _bad_host(u):
            continue
        if int(m.group(2)) < 300 or int(m.group(3)) < 300:
            continue
        out.append(_item(u, u, 'google'))
        if len(out) >= limit:
            break
    for m in re.finditer(r'"ou":"(https?://[^"]+)"', text):  # formato antiguo
        u = m.group(1)
        if not (_JUNK.search(u) or _bad_host(u)):
            out.append(_item(u, u, 'google'))
    return _unique(out)[:limit]


def _google_basic_pages(text, limit=10):
    """Versión sencilla de Google Imágenes: enlaces a las páginas donde está cada foto, en el orden de Google."""
    from urllib.parse import parse_qs
    pages = []
    for href in re.findall(r'href="(/url\?[^"]+)"', text):
        qs = parse_qs(urlparse(href.replace('&amp;', '&')).query)
        u = (qs.get('url') or qs.get('q') or [''])[0]
        if u.startswith('http') and 'google.' not in _host(u) and not _bad_host(u) and u not in pages:
            pages.append(u)
        if len(pages) >= limit:
            break
    return pages


def _og_image(page):
    try:
        r = requests.get(page, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text[:400000], 'html.parser')
        for attrs in ({'property': 'og:image'}, {'property': 'og:image:url'}, {'name': 'twitter:image'}):
            tag = soup.find('meta', attrs=attrs)
            if tag and tag.get('content'):
                u = full_size(urljoin(r.url, tag['content']))
                if u.startswith('http') and not _JUNK.search(u):
                    return _item(u, r.url, 'google')
    except Exception:
        pass
    return None


def google_images(query, limit=16):
    """Busca exactamente como en Google Imágenes y respeta el orden de Google.
    Con GOOGLE_SEARCH_API_KEY y GOOGLE_SEARCH_CX usa la API oficial; si no, lee la página de resultados."""
    out = []
    if GOOGLE_SEARCH_API_KEY and GOOGLE_SEARCH_CX:
        try:
            for start in (1, 11):
                r = requests.get('https://www.googleapis.com/customsearch/v1', timeout=20, params={
                    'key': GOOGLE_SEARCH_API_KEY, 'cx': GOOGLE_SEARCH_CX, 'q': query, 'searchType': 'image',
                    'num': 10, 'start': start, 'gl': 'es', 'hl': 'es', 'safe': 'active', 'imgSize': 'large'})
                r.raise_for_status()
                for it in r.json().get('items', []) or []:
                    u = it.get('link') or ''
                    if u.startswith('http') and not _JUNK.search(u) and not _bad_host(u):
                        out.append(_item(u, (it.get('image') or {}).get('contextLink') or u, 'google'))
                if len(out) >= limit:
                    break
            if out:
                return _unique(out)[:limit]
        except Exception:
            pass
    params = {'q': query, 'tbm': 'isch', 'hl': 'es', 'gl': 'es', 'safe': 'active'}
    try:  # 1) versión completa (trae la URL original de cada foto)
        r = requests.get('https://www.google.com/search', timeout=20, cookies=_GOOGLE_COOKIES,
                         params={**params, 'udm': '2'}, headers=HEADERS)
        out = _google_full_html(r.text, limit)
    except Exception:
        pass
    if len(out) < 4:
        try:  # 2) versión sencilla: se abre cada página del resultado y se coge su foto principal
            r = requests.get('https://www.google.com/search', timeout=20, cookies=_GOOGLE_COOKIES, params=params,
                             headers={'User-Agent': _BASIC_UA, 'Accept-Language': 'es-ES,es;q=0.9'})
            pages = _google_basic_pages(r.text, 10)
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=6) as pool:
                out += [x for x in pool.map(_og_image, pages) if x]
        except Exception:
            pass
    return _unique(out)[:limit]


def _fold(text):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFD', (text or '').lower()) if unicodedata.category(c) != 'Mn')


def rank(items, query):
    """Ordena como un buscador: primero las que tienen las palabras de la búsqueda en la foto o en su página.
    Dentro del mismo nivel se respeta el orden original (el de Google)."""
    words = [_fold(w) for w in keywords(query, 8)]
    def score(pair):
        index, item = pair
        hay = _fold(item.get('url', '') + ' ' + item.get('source', ''))
        hits = sum(1 for w in words if w and w in hay)
        local = 1 if any(k in hay for k in ('linea', 'campo-de-gibraltar', 'campogibraltar', 'europasur', 'lalinea')) else 0
        source = 3 if item.get('kind') == 'source' else 0
        return (-(source + hits + local), index)
    return [item for _, item in sorted(enumerate(items), key=score)]


def duckduckgo_images(query, limit=20):
    """DuckDuckGo Imágenes (resultados de toda la web, sin clave)."""
    out = []
    try:
        sess = requests.Session()
        sess.headers.update(HEADERS)
        page = sess.get('https://duckduckgo.com/', params={'q': query, 'iax': 'images', 'ia': 'images'}, timeout=15)
        m = re.search(r'vqd=["\']?([\d-]+)["\'&]', page.text) or re.search(r'"vqd":"([\d-]+)"', page.text)
        if not m:
            return out
        r = sess.get('https://duckduckgo.com/i.js', timeout=15, headers={'Referer': 'https://duckduckgo.com/', 'Accept': 'application/json'},
                     params={'l': 'es-es', 'o': 'json', 'q': query, 'vqd': m.group(1), 'f': ',,,,,', 'p': '1'})
        for it in (r.json().get('results') or []):
            u = it.get('image') or ''
            if not u.startswith('http') or _JUNK.search(u):
                continue
            if int(it.get('width') or 0) and (int(it.get('width') or 0) < 500 or int(it.get('height') or 0) < 350):
                continue
            out.append(_item(u, it.get('url') or u, 'internet'))
            if len(out) >= limit:
                break
    except Exception:
        pass
    return out


def internet_images(query, limit=20):
    """Busca fotos en toda la web como en Google Imágenes: Google primero; DuckDuckGo y Bing de reserva."""
    found = []
    for provider in (google_images, duckduckgo_images, web_images):
        try:
            found += [x for x in provider(query) if not _bad_host(x['url']) and not _bad_host(x.get('source', ''))]
        except Exception:
            pass
        if len(_unique(found)) >= limit:
            break
    return rank(_unique(found), query)[:limit]


def providers_check(query='La Línea de la Concepción playa'):
    """Cuántas fotos devuelve cada buscador desde el servidor (para Ajustes)."""
    out = []
    for name, fn in (('Google Imágenes', google_images), ('DuckDuckGo', duckduckgo_images), ('Bing', web_images)):
        try:
            res = fn(query)
            out.append({'name': name, 'count': None if res is None else len(res)})
        except Exception as exc:
            out.append({'name': name, 'count': 0, 'error': str(exc)[:120]})
    return out


def web_images(query, limit=10):
    """Búsqueda de imágenes en internet (Bing Imágenes)."""
    out = []
    try:
        r = requests.get('https://www.bing.com/images/search?q=' + quote_plus(query) + '&form=HDRSC2&first=1&setlang=es',
                         headers=HEADERS, timeout=20)
        soup = BeautifulSoup(r.text, 'html.parser')
        for a in soup.select('a.iusc')[:limit * 2]:
            try:
                meta = json.loads(a.get('m') or '{}')
            except ValueError:
                continue
            u, page = meta.get('murl'), meta.get('purl')
            if u and u.startswith('http') and not _JUNK.search(u):
                out.append(_item(u, page or u, 'web'))
            if len(out) >= limit:
                break
    except Exception:
        pass
    return out


def commons_images(query, limit=8):
    api = 'https://commons.wikimedia.org/w/api.php'
    params = {'action': 'query', 'generator': 'search', 'gsrsearch': query, 'gsrnamespace': 6, 'gsrlimit': 12,
              'prop': 'imageinfo', 'iiprop': 'url|extmetadata', 'iiurlwidth': 1800, 'format': 'json'}
    out = []
    terms = {w.lower() for w in keywords(query, 8)}
    try:
        d = requests.get(api, params=params, timeout=20, headers=HEADERS).json()
        for p in (d.get('query', {}).get('pages', {}) or {}).values():
            ii = (p.get('imageinfo') or [{}])[0]
            meta = ii.get('extmetadata') or {}
            u = ii.get('thumburl') or ii.get('url')
            if not u or _JUNK.search(u.split('/')[-1]):
                continue
            title = (p.get('title') or '').lower()
            if terms and not any(t in title for t in terms):
                continue  # descarta resultados sin relación con la noticia
            item = _item(u, ii.get('descriptionurl') or 'https://commons.wikimedia.org', 'commons')
            item['author'] = re.sub('<[^>]+>', '', (meta.get('Artist') or {}).get('value', ''))[:120]
            out.append(item)
            if len(out) >= limit:
                break
    except Exception:
        pass
    return out


_WP_SIZE = re.compile(r'-\d{2,4}x\d{2,4}(?=\.(jpe?g|png|webp)(\?|$))', re.I)


def full_size(url):
    """En WordPress la miniatura se llama foto-300x175.jpg; el original es foto.jpg."""
    return _WP_SIZE.sub('', url or '')


def _unique(items):
    seen, out = set(), []
    for x in items:
        key = re.sub(r'[?#].*$', '', x['url'])
        if key not in seen:
            seen.add(key)
            out.append(x)
    return out


_NOT_VISUAL = re.compile(r'^(toneladas|euros|millones|miles|metros|kilos|personas|años|meses|días|semanas|horas|'
                         r'.*(adas|ados|idas|idos|ará|arán|erá|erán)|anuncia|abre|pide|piden|recibe|presenta|aprueba)$', re.I)


def photo_query_for(title, section=''):
    words = [w for w in keywords(re.sub(r'\s+[-–|]\s+[^-–|]{3,40}$', '', title or ''), 10)
             if not _NOT_VISUAL.match(w) and not w.isdigit()][:4]
    return (' '.join(words) + ' La Línea') if words else 'La Línea de la Concepción ' + (section or '')


def search_real_photos(candidate, section='', query=None):
    """1) La foto que acompaña a la propia noticia (RSS o página). 2) Internet con una búsqueda descriptiva."""
    found = []
    hint = candidate.get('image_hint') or ''
    if hint:
        found += [_item(full_size(hint), candidate.get('url') or hint, 'source'), _item(hint, candidate.get('url') or hint, 'source')]
    real = resolve_google_news(candidate.get('url', ''))
    found += page_images(real, limit=3)
    title = candidate.get('title') or ''
    words = keywords(title, 7)
    query = (query or '').strip() or photo_query_for(title, section)
    if 'línea' not in query.lower() and 'linea' not in query.lower():
        query += ' La Línea'
    found += internet_images(query)
    broader = photo_query_for(title, section)
    if len(found) < 6 and broader.lower() != query.lower():
        found += internet_images(broader)  # segunda búsqueda con las palabras visuales del titular
    return _unique(found)[:24]


def search_photos(query):
    """Búsqueda libre desde el panel (toda la web)."""
    return internet_images(query, limit=24)


def download_image(url, min_width=500, min_height=350):
    r = requests.get(url, timeout=30, headers={**HEADERS, 'Referer': 'https://' + (_host(url) or 'google.com') + '/'})
    r.raise_for_status()
    im = Image.open(BytesIO(r.content))
    if im.width < min_width or im.height < min_height:
        raise ValueError('La imagen es demasiado pequeña (%sx%s)' % (im.width, im.height))
    im = im.convert('RGB')
    name = hashlib.sha1(url.encode()).hexdigest()[:16] + '.jpg'
    path = UPLOAD_DIR / name
    im.save(path, 'JPEG', quality=93)
    return str(path)


def first_usable(candidates, limit=12):
    """Descarga la primera foto válida (tamaño suficiente). Devuelve (item, ruta) o (None, '')."""
    for item in candidates[:limit]:
        try:
            return item, download_image(item['url'])
        except Exception:
            continue
    return None, ''
