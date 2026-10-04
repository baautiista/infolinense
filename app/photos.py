"""Búsqueda de fotos para la noticia.

Orden: foto de la propia noticia (og:image), fotos de otros medios que cuentan la
misma historia, búsqueda de imágenes en internet y, por último, Wikimedia Commons.
"""
import hashlib
import json
import re
from io import BytesIO
from urllib.parse import quote, quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image

from .config import UPLOAD_DIR
from . import db

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
        for img in soup.select('article img, main img, .article img, .entry-content img, figure img')[:15]:
            src = img.get('data-src') or img.get('data-lazy-src') or img.get('src') or ''
            if img.get('srcset') and not src.startswith('http'):
                src = img['srcset'].split(',')[-1].strip().split(' ')[0]
            try:
                if int(img.get('width') or 999) < 300:
                    continue
            except ValueError:
                pass
            if src and not src.startswith('data:'):
                found.append(urljoin(base, src))
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


def _unique(items):
    seen, out = set(), []
    for x in items:
        key = re.sub(r'[?#].*$', '', x['url'])
        if key not in seen:
            seen.add(key)
            out.append(x)
    return out


def search_real_photos(candidate, section=''):
    url = candidate.get('url', '')
    real = resolve_google_news(url)
    title = re.sub(r'\s+[-–|]\s+[^-–|]{3,40}$', '', candidate.get('title') or '')
    found = page_images(real)
    found += same_story_images(title, exclude_url=real)
    words = keywords(title, 6)
    query = ' '.join(words) + ' La Línea de la Concepción' if words else 'La Línea de la Concepción ' + (section or '')
    found += web_images(query)
    if len(found) < 6:
        found += commons_images(' '.join(words[:3]) + ' La Línea' if words else 'La Línea de la Concepción')
    return _unique(found)[:20]


def search_photos(query):
    """Búsqueda libre desde el panel."""
    return _unique(web_images(query, limit=14) + commons_images(query, limit=6))[:20]


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


def first_usable(candidates, limit=6):
    """Descarga la primera foto válida (tamaño suficiente). Devuelve (item, ruta) o (None, '')."""
    for item in candidates[:limit]:
        try:
            return item, download_image(item['url'])
        except Exception:
            continue
    return None, ''
