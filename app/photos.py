"""Búsqueda de fotos para la noticia.

Orden: foto de la propia noticia (og:image) y búsqueda en Google Imágenes, como la haría una persona.
Si Google no responde se usan DuckDuckGo y Bing como reserva.
"""
import hashlib
import json
import os
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
                    'num': 10, 'start': start, 'gl': 'es', 'hl': 'es', 'safe': 'active', 'imgSize': 'huge' if start == 1 else 'xlarge'})
                r.raise_for_status()
                for it in r.json().get('items', []) or []:
                    u = it.get('link') or ''
                    img = it.get('image') or {}
                    if u.startswith('http') and not _JUNK.search(u) and not _bad_host(u):
                        out.append(dict(_item(u, img.get('contextLink') or u, 'google'), w=img.get('width') or 0, h=img.get('height') or 0))
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


def _parse_bing(text, limit=30):
    """Resultados de Bing Imágenes: cada foto lleva m='{"purl":página,"murl":foto}'."""
    import html as _html
    out = []
    for raw in re.findall(r'\sm="([^"]+)"', text) + re.findall(r"\sm='([^']+)'", text):
        try:
            meta = json.loads(_html.unescape(raw))
        except ValueError:
            continue
        u, page = meta.get('murl') or '', meta.get('purl') or ''
        if u.startswith('http') and not _JUNK.search(u) and not _bad_host(u):
            out.append(_item(u, page or u, 'web'))
        if len(out) >= limit:
            break
    if not out:  # por si cambia el formato: buscar "murl" en el texto
        flat = _html.unescape(text).replace('\\/', '/')
        for u in re.findall(r'"murl":"(https?://[^"]+)"', flat)[:limit]:
            if not _JUNK.search(u) and not _bad_host(u):
                out.append(_item(u, u, 'web'))
    return _unique(out)


def web_images(query, limit=30):
    """Bing Imágenes (dos formatos de página, el segundo más ligero)."""
    out = []
    for url in ('https://www.bing.com/images/async?q=%s&first=0&count=35&mmasync=1&setlang=es&cc=es' % quote_plus(query),
                'https://www.bing.com/images/search?q=%s&form=HDRSC2&first=1&setlang=es&cc=es' % quote_plus(query)):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15, cookies={'SRCHHPGUSR': 'ADLT=STRICT'})
            out += _parse_bing(r.text, limit)
        except Exception:
            pass
        if len(out) >= 8:
            break
    return _unique(out)[:limit]


def _parse_yahoo(text, limit=30):
    import html as _html
    from urllib.parse import unquote
    out = []
    flat = _html.unescape(text).replace('\\/', '/')
    for u in re.findall(r'"iurl":"(https?://[^"]+)"', flat):
        out.append(_item(u, u, 'web'))
    for raw in re.findall(r'imgurl=([^&"\s]+)', flat):
        u = unquote(raw)
        if not u.startswith('http'):
            u = 'https://' + u
        out.append(_item(u, u, 'web'))
    return [x for x in _unique(out) if not _JUNK.search(x['url']) and not _bad_host(x['url'])][:limit]


def yahoo_images(query, limit=30):
    try:
        r = requests.get('https://images.search.yahoo.com/search/images', params={'p': query, 'ei': 'UTF-8'},
                         headers=HEADERS, timeout=15)
        return _parse_yahoo(r.text, limit)
    except Exception:
        return []


def openverse_images(query, limit=20):
    """Openverse: buscador abierto de fotos (Flickr y otros), funciona sin clave desde servidores."""
    out = []
    try:
        r = requests.get('https://api.openverse.org/v1/images/', timeout=15, headers=HEADERS,
                         params={'q': query, 'page_size': limit, 'mature': 'false'})
        for it in (r.json().get('results') or []):
            u = it.get('url') or ''
            if not u.startswith('http') or (it.get('width') and int(it.get('width') or 0) < 500):
                continue
            item = _item(u, it.get('foreign_landing_url') or u, 'internet')
            item['author'] = (it.get('creator') or '')[:120]
            out.append(item)
    except Exception:
        pass
    return out


def news_images(query, limit=10):
    """Fotos de noticias publicadas sobre el tema (Bing News y Google News): la foto principal de cada noticia."""
    pages = []
    try:
        r = requests.get('https://www.bing.com/news/search', params={'q': query, 'format': 'rss', 'setlang': 'es', 'cc': 'es'},
                         headers=HEADERS, timeout=15)
        pages += [l for l in re.findall(r'<link>(https?://[^<]+)</link>', r.text) if 'bing.com' not in _host(l)]
        for raw in re.findall(r'<link>(https?://www\.bing\.com/news/apiclick[^<]+)</link>', r.text):
            from urllib.parse import parse_qs, unquote
            real = (parse_qs(urlparse(raw.replace('&amp;', '&')).query).get('url') or [''])[0]
            if real:
                pages.append(unquote(real))
    except Exception:
        pass
    try:
        feed = 'https://news.google.com/rss/search?q=' + quote(query) + '&hl=es&gl=ES&ceid=ES:es'
        r = requests.get(feed, headers=HEADERS, timeout=15)
        pages += [resolve_google_news(l) for l in re.findall(r'<link>(https://news\.google\.com/rss/articles/[^<]+)</link>', r.text)[:4]]
    except Exception:
        pass
    pages = [p for p in dict.fromkeys(pages) if p.startswith('http') and 'news.google.com' not in p and not _bad_host(p)][:limit]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=6) as pool:
        found = [x for x in pool.map(_og_image, pages) if x]
    return [dict(x, kind='news') for x in found]


def local_images(query, limit=12):
    """Fotos de noticias que el panel ya ha recogido de los medios de La Línea (no depende de ningún buscador)."""
    words = [w for w in keywords(query, 5) if len(w) >= 4]
    if not words:
        return []
    out = []
    try:
        conds = ' OR '.join(['title LIKE ?'] * len(words))
        rows = db.rows('SELECT title,url,image_hint FROM candidates WHERE image_hint IS NOT NULL AND image_hint!="" AND (' + conds +
                       ') ORDER BY id DESC LIMIT 60', tuple('%' + w + '%' for w in words))
        folded = [_fold(w) for w in words]
        rows.sort(key=lambda r: -sum(1 for w in folded if w in _fold(r.get('title'))))
        for r in rows:
            out.append(_item(full_size(r['image_hint']), r.get('url') or r['image_hint'], 'news'))
            if len(out) >= limit:
                break
    except Exception:
        pass
    return out


def serpapi_images(query, limit=30):
    """Google Imágenes real a través de SerpApi (SERPAPI_KEY, 100 búsquedas gratis al mes). Mismo orden que Google."""
    key = os.getenv('SERPAPI_KEY', '').strip()
    if not key:
        return []
    r = requests.get('https://serpapi.com/search.json', timeout=25, params={
        'engine': 'google_images', 'q': query, 'hl': 'es', 'gl': 'es', 'api_key': key, 'imgsz': 'l'})
    r.raise_for_status()
    out = []
    for it in r.json().get('images_results', [])[:limit]:
        u = it.get('original') or ''
        if u.startswith('http') and not _JUNK.search(u):
            out.append(dict(_item(u, it.get('link') or u, 'google'), w=it.get('original_width') or 0, h=it.get('original_height') or 0))
    return out


def pexels_images(query, limit=20):
    """Pexels (PEXELS_API_KEY, gratis): fotos de gran calidad, en vertical si hay."""
    key = os.getenv('PEXELS_API_KEY', '').strip()
    if not key:
        return []
    r = requests.get('https://api.pexels.com/v1/search', timeout=20, headers={'Authorization': key},
                     params={'query': query, 'per_page': limit, 'locale': 'es-ES'})
    r.raise_for_status()
    return [dict(_item(p['src'].get('large2x') or p['src']['original'], p.get('url') or '', 'pexels'),
                 author=p.get('photographer') or '', w=p.get('width') or 0, h=p.get('height') or 0)
            for p in r.json().get('photos', [])]


PROVIDERS = (('Google (SerpApi)', serpapi_images), ('Google Imágenes', google_images), ('Pexels', pexels_images), ('Bing Imágenes', web_images), ('DuckDuckGo', duckduckgo_images),
             ('Yahoo Imágenes', yahoo_images), ('Noticias', news_images), ('Openverse', openverse_images),
             ('Fotos guardadas', local_images))


def _run_all(query, timeout=40):
    """Lanza todos los buscadores a la vez y junta los resultados (Google primero)."""
    from concurrent.futures import ThreadPoolExecutor, wait
    results = {}
    pool = ThreadPoolExecutor(max_workers=len(PROVIDERS))
    futures = {pool.submit(fn, query): name for name, fn in PROVIDERS}
    done, _ = wait(futures, timeout=timeout)
    pool.shutdown(wait=False, cancel_futures=True)  # no esperar a un buscador lento
    for f in done:
        try:
            results[futures[f]] = f.result() or []
        except Exception:
            results[futures[f]] = []
    merged = []
    for name, _ in PROVIDERS:
        merged += [x for x in results.get(name, []) if not _bad_host(x['url'])]
    return _unique(merged), results


def _variants(query):
    """Si una búsqueda no da nada, se prueba de forma más amplia, como haría una persona."""
    q = re.sub(r'\s+', ' ', query or '').strip()
    out = [q]
    plain = re.sub(r'\b(La L[ií]nea( de la Concepci[oó]n)?|Ayuntamiento)\b', '', q, flags=re.I).strip()
    if plain and plain.lower() != q.lower():
        out.append(plain)
    words = keywords(plain or q, 3)
    if words and ' '.join(words).lower() not in [x.lower() for x in out]:
        out.append(' '.join(words))
    if words:
        out.append(words[0])
    return list(dict.fromkeys(x for x in out if x))


def internet_images(query, limit=24):
    """Busca fotos en toda la web con varios buscadores a la vez; si no hay resultados, amplía la búsqueda."""
    found = []
    for q in _variants(query):
        merged, _ = _run_all(q)
        found = _unique(found + rank(merged, query))
        if len(found) >= 8:
            break
    return found[:limit]


def providers_check(query='patinete eléctrico La Línea'):
    """Cuántas fotos devuelve cada buscador desde el servidor (para Ajustes)."""
    _, results = _run_all(query)
    return [{'name': name, 'count': len(results.get(name, []))} for name, _ in PROVIDERS]


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
    """Búsqueda libre desde el panel: lo mismo que saldría en Google, en su orden, y luego el resto de buscadores.
    Se quitan las fotos pequeñas conocidas (la imagen final es 1080x1350)."""
    merged, results = _run_all(query)
    google = [x for name in ('Google (SerpApi)', 'Google Imágenes') for x in results.get(name, [])]
    rest = rank([x for x in merged if x not in google], query)
    out = _unique(google + rest)
    if len(out) < 8:
        out = _unique(out + internet_images(query))
    return [x for x in out if not (x.get('w') and x.get('w') < 700)][:30]


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
    """Descarga la primera foto de buena calidad para 1080x1350 (al menos 1000 px de ancho);
    si ninguna llega, la primera aceptable (700 px). Devuelve (item, ruta) o (None, '')."""
    pool = sorted(candidates[:limit + 8], key=lambda x: 0 if (x.get('w') or 0) >= 1000 or x.get('kind') == 'source' else 1)
    for min_w, min_h in ((1000, 750), (700, 500)):
        for item in pool[:limit]:
            if item.get('w') and item['w'] < min_w:
                continue
            try:
                return item, download_image(item['url'], min_width=min_w, min_height=min_h)
            except Exception:
                continue
    return None, ''
