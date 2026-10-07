"""Canva: conexión OAuth y carrusel desde la plantilla de marca de Área (autocompletar + exportar PNG).

Convención de campos en la plantilla (Aplicaciones → Autocompletar → nombres de los cuadros):
  TITULO_1, TEXTO_1, FOTO_1, ANTETITULO_1 … TITULO_8, TEXTO_8, FOTO_8  (el número es la diapositiva; 1 = portada)
  También vale TITULAR/HEADLINE, ENTRADILLA/SUMMARY/TEXT, IMAGEN/PHOTO, KICKER/SECCION, MUNICIPIO, NUM.
  Un campo sin número es de la portada (MUNICIPIO sin número va en todas).
"""
import base64
import hashlib
import json
import re
import secrets
import threading
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlencode, urlparse

import requests

from . import db
from .config import PUBLIC_BASE_URL, RENDER_DIR, CANVA_CLIENT_ID as CLIENT_ID, CANVA_CLIENT_SECRET as CLIENT_SECRET, CANVA_TEMPLATE_ID


def template_id():
    return db.setting('canva_template') or CANVA_TEMPLATE_ID


def ready():
    return bool(CLIENT_ID and CLIENT_SECRET and PUBLIC_BASE_URL)


API = 'https://api.canva.com/rest/v1'
SCOPES = 'asset:read asset:write brandtemplate:content:read design:content:read design:content:write design:meta:read'
_lock = threading.RLock()


def connected():
    return db.row('SELECT 1 FROM canva_oauth WHERE id=1') is not None


def callback_url():
    return PUBLIC_BASE_URL + '/api/canva/callback'


def authorization_url():
    if not ready():
        raise ValueError('Configura CANVA_CLIENT_ID, CANVA_CLIENT_SECRET y PUBLIC_BASE_URL en Railway')
    state = secrets.token_urlsafe(40)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    db.exec_('DELETE FROM canva_oauth_state WHERE expires_at<?', (int(time.time()),))
    db.exec_('INSERT INTO canva_oauth_state VALUES(?,?,?)', (state, verifier, int(time.time()) + 600))
    return 'https://www.canva.com/api/oauth/authorize?' + urlencode({
        'code_challenge': challenge, 'code_challenge_method': 'S256',
        'scope': SCOPES, 'response_type': 'code', 'client_id': CLIENT_ID,
        'state': state, 'redirect_uri': callback_url(),
    })


def _token_request(form):
    resp = requests.post(API + '/oauth/token', data=form,
                         auth=(CLIENT_ID, CLIENT_SECRET), timeout=25)
    if not resp.ok:
        raise ValueError('Canva rechazó la autorización (%s)' % resp.status_code)
    data = resp.json()
    if not data.get('access_token') or not data.get('refresh_token'):
        raise ValueError('Canva no devolvió los tokens necesarios')
    return data


def _save_token(data):
    with _lock:
        db.exec_('INSERT OR REPLACE INTO canva_oauth VALUES(1,?,?,?)',
                 (data['access_token'], data['refresh_token'],
                  int(time.time()) + int(data.get('expires_in', 3600))))


def complete(code, state):
    row = db.row('SELECT verifier,expires_at FROM canva_oauth_state WHERE state=?', (state,))
    if not row or row['expires_at'] < int(time.time()):
        raise ValueError('Autorización caducada o no iniciada desde Área Desk')
    db.exec_('DELETE FROM canva_oauth_state WHERE state=?', (state,))
    data = _token_request({'grant_type': 'authorization_code', 'code': code,
                           'code_verifier': row['verifier'], 'redirect_uri': callback_url()})
    _save_token(data)


def access_token():
    with _lock:
        row = db.row('SELECT * FROM canva_oauth WHERE id=1')
        if not row:
            raise ValueError('Conecta tu cuenta de Canva desde Ajustes')
        if row['expires_at'] > time.time() + 120:
            return row['access_token']
        data = _token_request({'grant_type': 'refresh_token', 'refresh_token': row['refresh_token']})
        _save_token(data)
        return data['access_token']


def _api(method, path, **kwargs):
    try:
        resp = requests.request(method, API + path,
                                headers={'Authorization': 'Bearer ' + access_token(), **kwargs.pop('headers', {})},
                                timeout=45, **kwargs)
        if not resp.ok:
            payload = resp.json()
            detail = payload.get('error') or payload
            if isinstance(detail, dict):
                detail = detail.get('message') or detail.get('code')
            raise ValueError('Canva (%s): %s' % (resp.status_code, str(detail or 'revisa permisos y configuración')[:180]))
        return resp.json()
    except requests.RequestException as exc:
        raise ValueError('No se pudo contactar con Canva') from exc


def _wait(path, first=None):
    job = (first or {}).get('job') or {}
    for attempt in range(40):
        if attempt or not job.get('status'):
            job = _api('GET', path).get('job', {})
        if job.get('status') == 'success':
            return job
        if job.get('status') == 'failed':
            err = job.get('error') or {}
            detail = err.get('message') or err.get('code') if isinstance(err, dict) else ''
            raise ValueError('Canva no pudo completar el trabajo' + (': ' + str(detail)[:180] if detail else ''))
        time.sleep(2)
    raise ValueError('Canva aún procesa el diseño; vuelve a intentarlo en unos segundos')


def _design_from_job(job):
    """Canva devuelve el diseño en job.result.design (versiones antiguas: job.design)."""
    result = job.get('result') or {}
    design = result.get('design') or job.get('design') or {}
    return design if isinstance(design, dict) else {}




def _norm(name):
    t = unicodedata.normalize('NFD', str(name or '').upper())
    return re.sub(r'[^A-Z0-9]', '', ''.join(c for c in t if unicodedata.category(c) != 'Mn'))


ROLES = [('title', ('TITULO', 'TITULAR', 'HEADLINE', 'TITLE')), ('text', ('TEXTO', 'ENTRADILLA', 'SUMMARY', 'TEXT', 'CUERPO')),
         ('photo', ('FOTO', 'IMAGEN', 'PHOTO', 'IMAGE')), ('kicker', ('ANTETITULO', 'KICKER', 'SECCION', 'SECTION', 'ETIQUETA')),
         ('town', ('MUNICIPIO', 'TOWN', 'LOCALIDAD')), ('num', ('NUMERO', 'NUM', 'PAGINA'))]


def field_role(name):
    """«TITULO_3» → ('title', 3); «FOTO» → ('photo', None)."""
    n = _norm(name)
    m = re.match(r'^(?:S|D|SLIDE|DIAPO|DIAPOSITIVA)?(\d+)?([A-Z]+?)(\d+)?$', n)
    if not m:
        return None, None
    word, num = m.group(2), m.group(1) or m.group(3)
    for role, words in ROLES:
        if word in words:
            return role, int(num) if num else None
    return None, None


def template_fields():
    tid = template_id()
    if not tid:
        raise ValueError('Pega el enlace de la plantilla de marca del carrusel en Ajustes → Canva')
    dataset = _api('GET', '/brand-templates/' + tid + '/dataset').get('dataset', {})
    out = []
    for name, info in dataset.items():
        role, num = field_role(name)
        out.append({'name': name, 'type': info.get('type'), 'role': role, 'slide': num})
    return out


def _upload(path, label):
    data = Path(path).read_bytes()
    if len(data) > 20_000_000:
        raise ValueError('Una foto supera 20 MB')
    meta = json.dumps({'name_base64': base64.b64encode(label.encode()).decode()})
    up = _api('POST', '/asset-uploads', data=data, headers={'Content-Type': 'application/octet-stream', 'Asset-Upload-Metadata': meta})
    done = _wait('/asset-uploads/' + up['job']['id'], up)
    asset = done.get('asset') or (done.get('result') or {}).get('asset') or {}
    if not asset.get('id'):
        raise ValueError('Canva no devolvió la foto subida')
    return asset['id']


def build_data(fields, slides, photo_paths, town, article_id):
    """Datos del autocompletado: cada campo con lo de su diapositiva."""
    data, uploads = {}, {}
    for f in fields:
        role, num = f['role'], f['slide']
        if not role:
            continue
        idx = (num or 1) - 1
        slide = slides[idx] if 0 <= idx < len(slides) else None
        if f['type'] == 'text':
            if role == 'town':
                value = town
            elif role == 'num':
                value = '%s/%s' % (idx + 1, len(slides)) if slide else ' '
            elif slide is None:
                value = ' '
            else:
                value = slide.get(role) or ' '
            data[f['name']] = {'type': 'text', 'text': value}
        elif f['type'] == 'image' and role == 'photo' and photo_paths:
            path = photo_paths[idx % len(photo_paths)]
            if path not in uploads:
                uploads[path] = _upload(path, 'Area-%s-%s%s' % (article_id, len(uploads) + 1, Path(path).suffix))
            data[f['name']] = {'type': 'image', 'asset_id': uploads[path]}
    return data


def _export_pages(design_id, article_id, pages):
    export = _api('POST', '/exports', json={'design_id': design_id, 'format': {'type': 'png', 'pages': pages}})
    urls = _wait('/exports/' + export['job']['id']).get('urls') or []
    out = []
    for n, u in enumerate(urls, start=1):
        host = urlparse(u).hostname or ''
        if urlparse(u).scheme != 'https' or not (host == 'canva.com' or host.endswith('.canva.com')):
            raise ValueError('Canva devolvió una URL de descarga inesperada')
        r = requests.get(u, timeout=60)
        r.raise_for_status()
        path = RENDER_DIR / ('area_%s_%02d.png' % (article_id, n))
        path.write_bytes(r.content)
        out.append(str(path))
    return out


def create_carousel(article, slides, photo_paths):
    """Autocompleta la plantilla con el carrusel, exporta cada página a PNG y lo guarda como imágenes del artículo."""
    if not ready() or not connected():
        raise ValueError('Conecta Canva desde Ajustes')
    fields = template_fields()
    if not any(f['role'] for f in fields):
        raise ValueError('La plantilla no tiene campos con nombres conocidos (TITULO_1, TEXTO_1, FOTO_1…). Revisa Ajustes → Canva.')
    data = build_data(fields, slides, photo_paths, article.get('town') or 'Campo de Gibraltar', article['id'])
    job = _api('POST', '/autofills', json={'type': 'create_from_brand_template', 'brand_template_id': template_id(),
                                          'title': 'Área · ' + (article.get('headline') or '')[:100], 'data': data})
    design = _design_from_job(_wait('/autofills/' + job['job']['id'], job))
    if not design.get('id'):
        raise ValueError('Canva no devolvió el diseño')
    url = design.get('url') or (design.get('urls') or {}).get('edit_url') or 'https://www.canva.com/design/%s/edit' % design['id']
    db.exec_('UPDATE articles SET canva_design_id=?,canva_url=?,canva_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?',
             (design['id'], url, article['id']))
    return export_carousel(article['id'], design['id'], len(slides), url)


def export_carousel(article_id, design_id, n_slides, url=''):
    for old in RENDER_DIR.glob('area_%s_*.png' % article_id):
        try:
            old.unlink()
        except OSError:
            pass
    db.exec_("DELETE FROM media WHERE article_id=? AND kind='canva'", (article_id,))
    paths = _export_pages(design_id, article_id, list(range(1, max(1, n_slides) + 1)))
    for n, p in enumerate(paths, start=1):
        db.exec_("INSERT INTO media(article_id,kind,url,local_path,caption,slide,selected) VALUES(?,?,?,?,?,?,1)",
                 (article_id, 'canva', url, p, 'Diapositiva %s' % n, n))
    return {'url': url, 'design_id': design_id, 'pages': len(paths)}


def template_from_link(value):
    value = (value or '').strip()
    m = re.search(r'brand-templates/([A-Za-z0-9_-]{8,})', value) or re.search(r'[?&]template=([A-Za-z0-9_-]{8,})', value) \
        or re.fullmatch(r'([A-Za-z0-9_-]{8,})', value)
    return m.group(1) if m else ''
