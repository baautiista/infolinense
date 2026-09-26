"""Canva OAuth and brand-template autofill. Tokens remain on the server volume."""
import base64
import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

from . import db
from .config import PUBLIC_BASE_URL

CLIENT_ID = os.getenv('CANVA_CLIENT_ID', '').strip()
CLIENT_SECRET = os.getenv('CANVA_CLIENT_SECRET', '').strip()
TEMPLATE_ID = os.getenv('CANVA_BRAND_TEMPLATE_ID', '').strip()
API = 'https://api.canva.com/rest/v1'
SCOPES = 'asset:read asset:write brandtemplate:content:read design:content:read design:content:write design:meta:read'
_lock = threading.RLock()


def ready():
    return bool(CLIENT_ID and CLIENT_SECRET and TEMPLATE_ID and PUBLIC_BASE_URL)


def connected():
    return db.row('SELECT 1 FROM canva_oauth WHERE id=1') is not None


def callback_url():
    return PUBLIC_BASE_URL + '/api/canva/callback'


def authorization_url():
    if not ready():
        raise ValueError('Configura CANVA_CLIENT_ID, CANVA_CLIENT_SECRET y CANVA_BRAND_TEMPLATE_ID')
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
        raise ValueError('Autorización caducada o no iniciada desde InfoLinense Desk')
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


def _wait(path):
    for _ in range(25):
        job = _api('GET', path).get('job', {})
        if job.get('status') == 'success':
            return job
        if job.get('status') == 'failed':
            raise ValueError('Canva no pudo completar el diseño o la carga de la foto')
        time.sleep(2)
    raise ValueError('Canva aún procesa el diseño; vuelve a intentarlo en unos segundos')


def template_status():
    dataset = _api('GET', '/brand-templates/' + TEMPLATE_ID + '/dataset').get('dataset', {})
    expected = {'HEADLINE': 'text', 'SUMMARY': 'text', 'SECTION': 'text', 'PHOTO': 'image'}
    return {'valid': all(dataset.get(k, {}).get('type') == v for k, v in expected.items()),
            'fields': {k: dataset.get(k, {}).get('type') for k in expected}}


def permission_status():
    if not connected():
        return {'connected': False, 'can_export_png': False}
    try:
        response = requests.post(API + '/oauth/introspect',
                                 auth=(CLIENT_ID, CLIENT_SECRET),
                                 data={'token': access_token()}, timeout=20)
        response.raise_for_status()
        payload = response.json()
        scopes = set((payload.get('scope') or '').split())
        return {'connected': bool(payload.get('active')),
                'can_export_png': 'design:content:read' in scopes}
    except requests.RequestException as exc:
        raise ValueError('No se pudieron comprobar los permisos de Canva') from exc


def create_design(article):
    if not connected():
        raise ValueError('Conecta tu cuenta de Canva desde Ajustes')
    photo = Path(article.get('image_local') or '')
    if not photo.is_file() or not article.get('image_license'):
        raise ValueError('Selecciona primero una fotografía real con licencia')
    allowed = json.loads(article.get('image_candidates_json') or '[]')
    if not any(p.get('url') == article.get('image_url') and p.get('publish_safe') for p in allowed):
        raise ValueError('La fotografía elegida no tiene permiso de reutilización verificado')
    schema = _api('GET', '/brand-templates/' + TEMPLATE_ID + '/dataset').get('dataset', {})
    expected = {'HEADLINE': 'text', 'SUMMARY': 'text', 'SECTION': 'text', 'PHOTO': 'image'}
    if any(schema.get(key, {}).get('type') != kind for key, kind in expected.items()):
        raise ValueError('Han cambiado los campos de la plantilla de Canva')
    data = photo.read_bytes()
    if len(data) > 20_000_000:
        raise ValueError('La fotografía supera 20 MB')
    filename = 'InfoLinense-' + str(article['id']) + photo.suffix
    metadata = json.dumps({'name_base64': base64.b64encode(filename.encode()).decode()})
    upload = _api('POST', '/asset-uploads', data=data,
                  headers={'Content-Type': 'application/octet-stream', 'Asset-Upload-Metadata': metadata})
    asset = _wait('/asset-uploads/' + upload['job']['id']).get('asset', {})
    if not asset.get('id'):
        raise ValueError('Canva no devolvió la fotografía cargada')
    fields = {
        'HEADLINE': {'type': 'text', 'text': article.get('headline') or ''},
        'SUMMARY': {'type': 'text', 'text': article.get('graphic_summary') or article.get('subtitle') or ''},
        'SECTION': {'type': 'text', 'text': article.get('section') or ''},
        'PHOTO': {'type': 'image', 'asset_id': asset['id']},
    }
    result = _api('POST', '/autofills', json={'type': 'create_from_brand_template',
                    'brand_template_id': TEMPLATE_ID,
                    'title': 'InfoLinense · ' + (article.get('headline') or '')[:100], 'data': fields})
    design = _wait('/autofills/' + result['job']['id']).get('design', {})
    url = design.get('urls', {}).get('edit_url') or design.get('urls', {}).get('view_url')
    if not url and design.get('id'):
        url = 'https://www.canva.com/design/' + design['id']
    if not url:
        raise ValueError('Canva terminó el trabajo pero no devolvió el enlace del diseño')
    result = {'url': url, 'design_id': design.get('id'), 'exported': False}
    db.exec_('INSERT OR REPLACE INTO canva_designs(article_id,design_id,url,exported,updated_at) VALUES(?,?,?,0,CURRENT_TIMESTAMP)',
             (article['id'], design['id'], url))
    # The PNG on the server is the final artwork used by previews and downloads.
    # A Canva app authorized before design:content:read was enabled can still
    # create the design; report that reconnection is needed for the export.
    try:
        from io import BytesIO
        from PIL import Image
        from .config import RENDER_DIR
        export = _api('POST', '/exports', json={'design_id': design['id'],
                    'format': {'type': 'png', 'pages': [1]}})
        urls = _wait('/exports/' + export['job']['id']).get('urls') or []
        if not urls:
            raise ValueError('Canva no devolvió la imagen exportada')
        from urllib.parse import urlparse
        host = urlparse(urls[0]).hostname or ''
        if urlparse(urls[0]).scheme != 'https' or not (host == 'canva.com' or host.endswith('.canva.com')):
            raise ValueError('Canva devolvió una URL de descarga inesperada')
        response = requests.get(urls[0], timeout=45)
        response.raise_for_status()
        if len(response.content) > 20_000_000:
            raise ValueError('La imagen exportada supera 20 MB')
        image = Image.open(BytesIO(response.content))
        image.verify()
        output = RENDER_DIR / ('article_%s.png' % article['id'])
        temp = output.with_suffix('.tmp')
        temp.write_bytes(response.content)
        temp.replace(output)
        result['exported'] = True
        db.exec_('UPDATE articles SET render_path=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                 (str(output), article['id']))
        db.exec_('UPDATE canva_designs SET exported=1,updated_at=CURRENT_TIMESTAMP WHERE article_id=?',
                 (article['id'],))
    except (ValueError, requests.RequestException, KeyError, OSError) as exc:
        result['export_error'] = str(exc)
    return result
