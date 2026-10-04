"""Panel de redes: quejas vecinales y noticias en páginas y grupos públicos de Facebook de La Línea.

Facebook no permite leer grupos sin permiso de Meta, así que se trabaja con lo que es público:
1. Publicaciones indexadas por Google de las páginas y grupos que vigilas (Ajustes → Redes).
2. Búsqueda de quejas recientes en Facebook sobre La Línea (Google, últimos 3 días).
3. Si ChatGPT tiene la búsqueda web activada, un barrido extra de quejas y avisos públicos.
"""
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs, unquote

import requests
from bs4 import BeautifulSoup

from . import db, sources

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36',
           'Accept-Language': 'es-ES,es;q=0.9'}
COOKIES = {'CONSENT': 'YES+cb.20240101-00-p0.es+FX+000', 'SOCS': 'CAESHAgBEhJnd3NfMjAyNDAxMDEtMF9SQzIaAmVzIAEaBgiA_LqsBg'}
COMPLAINT = re.compile(r'\b(quej|denunci|vergüenz|verguenz|indignad|harto|hartos|abandon|suciedad|basura|ratas|cucarach|bache|socav|'
                       r'sin luz|apagad|farola|sin agua|fuga|atasco|cola|aparcamiento|ruido|okupa|inseguridad|robo|peligro|'
                       r'roto|rota|caído|caida|desbord|inund|olor|mosquit|plaga|reclam|protesta|vecinos piden|nadie hace)', re.I)
SOURCE_NAME = 'Redes · quejas vecinales'


def kind_of(text):
    return 'queja' if COMPLAINT.search(text or '') else 'noticia'


def _social_source_id():
    row = db.row('SELECT id FROM sources WHERE name=?', (SOURCE_NAME,))
    if row:
        return row['id']
    return db.exec_("INSERT INTO sources(name,url,kind,priority,official,local_scope,active) VALUES(?,?,?,?,?,?,1)",
                    (SOURCE_NAME, 'https://www.facebook.com/search/posts/?q=La%20L%C3%ADnea', 'social', 60, 0, 0))


def google_web(query, days=3, limit=20):
    """Resultados de Google (web) de los últimos `days` días: [{title,url,snippet}]."""
    out = []
    try:
        r = requests.get('https://www.google.com/search', headers=HEADERS, cookies=COOKIES, timeout=20,
                         params={'q': query, 'hl': 'es', 'gl': 'es', 'num': 30, 'tbs': 'qdr:d%d' % days})
        soup = BeautifulSoup(r.text, 'html.parser')
        for a in soup.select('a[href]'):
            href = a['href']
            if href.startswith('/url?'):
                href = unquote(parse_qs(urlparse(href).query).get('q', [''])[0])
            host = (urlparse(href).hostname or '')
            if 'facebook.com' not in host:
                continue
            h3 = a.find('h3')
            title = (h3.get_text(' ', strip=True) if h3 else a.get_text(' ', strip=True))[:240]
            block = a.find_parent('div')
            snippet = ''
            for _ in range(4):
                if block is None:
                    break
                text = block.get_text(' ', strip=True)
                if len(text) > len(title) + 40:
                    snippet = text[:600]
                    break
                block = block.find_parent('div')
            if title and href not in {x['url'] for x in out}:
                out.append({'title': title, 'url': href.split('?')[0] if '/groups/' not in href else href, 'snippet': snippet})
            if len(out) >= limit:
                break
    except Exception:
        pass
    return out


def watched():
    """Páginas y grupos de Facebook/Instagram que vigilas."""
    return db.rows("SELECT * FROM sources WHERE kind='social' AND name!=? ORDER BY name", (SOURCE_NAME,))


def _page_name(url):
    path = [p for p in urlparse(url).path.split('/') if p]
    if path[:1] == ['groups'] and len(path) > 1:
        return 'Grupo ' + path[1]
    return path[0] if path else 'Facebook'


def _add(item, sid, outlet):
    text = item['title'] + ' ' + item.get('snippet', '')
    if not sources.exact_locality(text) and 'linea' not in outlet.lower() and 'línea' not in outlet.lower():
        return None
    cid = sources.add_candidate(item['title'], item['url'], item.get('snippet', ''), SOURCE_NAME, sid,
                                datetime.now(timezone.utc).isoformat(timespec='seconds'),
                                {'priority': 60, 'official': 0, 'local_scope': 1}, outlet=outlet)
    if cid:
        db.exec_('UPDATE candidates SET social_type=? WHERE id=?', (kind_of(text), cid))
    return cid


def scan():
    """Investiga las redes ahora. Devuelve cuántas publicaciones nuevas ha encontrado."""
    sid = _social_source_id()
    added, errors = [], []
    # 1. Páginas y grupos vigilados
    for src in watched():
        if not src.get('active'):
            continue
        path = urlparse(src['url']).path.strip('/')
        target = '/'.join(path.split('/')[:2]) if path.startswith('groups/') else path.split('/')[0]
        host = urlparse(src['url']).hostname or 'facebook.com'
        for item in google_web(f'site:{host}/{target}', days=3, limit=15):
            cid = _add(item, sid, src['name'])
            if cid: added.append(cid)
    # 2. Quejas vecinales públicas
    for q in ('site:facebook.com "La Línea" vecinos (queja OR denuncian OR quejan OR reclaman)',
              'site:facebook.com "La Línea de la Concepción" (basura OR baches OR "sin luz" OR ratas OR suciedad OR inseguridad)',
              'site:facebook.com/groups "La Línea" (queja OR denuncia OR vergüenza OR abandono)'):
        for item in google_web(q, days=3, limit=15):
            cid = _add(item, sid, _page_name(item['url']))
            if cid: added.append(cid)
    # 3. Barrido con ChatGPT (búsqueda web), si está disponible
    try:
        from . import ai
        if any(ai.web_enabled(p) for p in ai.provider_chain()):
            prompt = ('Busca publicaciones PÚBLICAS de los últimos 3 días en páginas y grupos de Facebook (y otras redes) sobre La Línea de la Concepción '
                      'con quejas de vecinos (limpieza, baches, alumbrado, agua, seguridad, ruidos, tráfico, servicios) o avisos y noticias locales. '
                      'Solo resultados reales con URL que hayas visto. Devuelve JSON {"items":[{"title","url","page_name","summary","type":"queja|noticia"}]} con máximo 12.')
            data, _, _ = ai.ask_json(ai.SYSTEM, prompt, web=True, web_required=True, max_tokens=3000)
            for it in (data.get('items') or [])[:12]:
                if str(it.get('url', '')).startswith('http'):
                    cid = _add({'title': it.get('title', ''), 'url': it['url'], 'snippet': it.get('summary', '')}, sid, it.get('page_name') or _page_name(it['url']))
                    if cid:
                        added.append(cid)
                        if it.get('type') in ('queja', 'noticia'):
                            db.exec_('UPDATE candidates SET social_type=? WHERE id=?', (it['type'], cid))
    except Exception as exc:
        errors.append('Búsqueda con IA: ' + str(exc)[:200])
    db.log('social_scan', f'Redes: {len(added)} publicaciones nuevas')
    return {'added': added, 'errors': errors}


def items():
    rows = db.rows("""SELECT c.*,s.kind AS source_kind,a.id AS article_id,a.status AS article_status
                      FROM candidates c LEFT JOIN sources s ON s.id=c.source_id
                      LEFT JOIN articles a ON a.candidate_id=c.id AND a.status!='rejected'
                      WHERE (s.kind='social' OR c.social_type IS NOT NULL OR c.url LIKE '%facebook.com%' OR c.url LIKE '%instagram.com%')
                        AND c.status NOT IN ('archived','published')""")
    limit = datetime.now(timezone.utc) - timedelta(days=4)
    out = []
    for r in rows:
        d = sources.publication_datetime(r.get('published_at')) or sources.publication_datetime((r.get('created_at') or '').replace(' ', 'T') + '+00:00')
        if d and d < limit:
            continue
        r['date_iso'] = d.isoformat() if d else None
        r['social_type'] = r.get('social_type') or kind_of((r.get('title') or '') + ' ' + (r.get('excerpt') or ''))
        r['group'] = 'Redes sociales'
        out.append(r)
    out.sort(key=lambda r: r.get('date_iso') or '', reverse=True)
    return out
