"""Canva OAuth and brand-template autofill. Tokens remain on the server volume."""
import base64
import hashlib
import json
import re
import os
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

from . import db, layout
from .config import PUBLIC_BASE_URL, RENDER_DIR

CLIENT_ID = os.getenv('CANVA_CLIENT_ID', '').strip()
CLIENT_SECRET = os.getenv('CANVA_CLIENT_SECRET', '').strip()
TEMPLATE_ID = os.getenv('CANVA_BRAND_TEMPLATE_ID', '').strip()
# Variantes de la plantilla según las líneas del titular: la entradilla queda fija abajo,
# el titular crece hacia arriba y la sección se coloca justo encima.
_DEFAULT_VARIANTS = {'EAHWTjWEEnA': {1: 'EAHXJaqiN5w', 2: 'EAHXJVlUZ2g', 4: 'EAHXJyNUgnU'}}
TEMPLATE_BY_LINES = {
    1: os.getenv('CANVA_TEMPLATE_1_LINE', '').strip() or _DEFAULT_VARIANTS.get(TEMPLATE_ID, {}).get(1, ''),
    2: os.getenv('CANVA_TEMPLATE_2_LINES', '').strip() or _DEFAULT_VARIANTS.get(TEMPLATE_ID, {}).get(2, ''),
    4: os.getenv('CANVA_TEMPLATE_4_LINES', '').strip() or _DEFAULT_VARIANTS.get(TEMPLATE_ID, {}).get(4, ''),
}


def _brand_templates(brand):
    """Plantillas del medio: {main, 1, 2, 4}. InfoLinense usa las de Railway si no se han cambiado en Ajustes."""
    from . import brands
    t = brands.settings(brand)['templates']
    if brands.valid(brand) == brands.DEFAULT:
        return {'main': t.get('main') or TEMPLATE_ID, '1': t.get('1') or TEMPLATE_BY_LINES.get(1, ''),
                '2': t.get('2') or TEMPLATE_BY_LINES.get(2, ''), '4': t.get('4') or TEMPLATE_BY_LINES.get(4, '')}
    return t


def template_for(headline, brand='infolinense'):
    t = _brand_templates(brand)
    lines = len(layout.headline_lines(headline))
    return t.get(str(lines)) or t.get('main') or ''
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


def _content_hash(article):
    fields = [article.get(k) or '' for k in ('headline', 'subtitle', 'section', 'image_url')]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()


def _is_default(article):
    from . import brands
    return brands.valid(article.get('brand')) == brands.DEFAULT


def _page_for(article):
    if not _is_default(article):  # otros medios: la página indicada en Ajustes → Medios (normalmente la 1)
        from . import brands
        return int(brands.settings(article.get('brand'))['page'] or 1)
    return layout.family_for(article.get('section'), article.get('headline'))[1]


def design_fields(article, strict=True):
    """Textos de la plantilla: exactamente el titular y la entradilla elegidos en Revisar.
    Si no caben, no se cambian: se avisa para que se acorten en Revisar (strict).
    Las diapositivas del carrusel (strict=False) sí se ajustan solas."""
    family = layout.family_for(article.get('section'), article.get('headline'))
    headline = re.sub(r'\s+', ' ', article.get('headline') or '').strip()
    summary = re.sub(r'\s+', ' ', article.get('subtitle') or '').strip()
    if strict:
        lines = len(layout.headline_lines(headline))
        if lines > layout.HEADLINE_MAX_LINES:
            raise ValueError('El titular ocupa %s líneas en la imagen y caben %s. Acórtalo en Revisar y vuelve a crear la imagen.'
                             % (lines, layout.HEADLINE_MAX_LINES))
        if len(summary) > layout.SUMMARY_MAX:
            raise ValueError('La entradilla tiene %s caracteres y en la imagen caben %s. Acórtala en Revisar y vuelve a crear la imagen.'
                             % (len(summary), layout.SUMMARY_MAX))
    else:
        if not layout.headline_fits(headline):
            headline = layout.fit_headline(headline)
        summary = layout.fit_summary(summary)
    if not _is_default(article):
        return {'HEADLINE': headline, 'SUMMARY': summary, 'SECTION': (article.get('section') or '').strip().upper(),
                'page': _page_for(article), 'template': template_for(headline, article.get('brand'))}
    return {'HEADLINE': headline, 'SUMMARY': summary, 'SECTION': family[0], 'page': family[1],
            'template': template_for(headline)}


def _export_png(article, design_id, output_suffix=None, mark_main=True, page=None):
    from io import BytesIO
    from PIL import Image
    from urllib.parse import urlparse

    export = _api('POST', '/exports', json={'design_id': design_id,
                  'format': {'type': 'png', 'pages': [int(page or _page_for(article))]}})
    urls = _wait('/exports/' + export['job']['id']).get('urls') or []
    if not urls:
        raise ValueError('Canva no devolvió la imagen exportada')
    host = urlparse(urls[0]).hostname or ''
    if urlparse(urls[0]).scheme != 'https' or not (host == 'canva.com' or host.endswith('.canva.com')):
        raise ValueError('Canva devolvió una URL de descarga inesperada')
    try:
        response = requests.get(urls[0], timeout=45)
        response.raise_for_status()
        if len(response.content) > 20_000_000:
            raise ValueError('La imagen exportada supera 20 MB')
        image = Image.open(BytesIO(response.content))
        if image.format != 'PNG':
            raise ValueError('Canva no devolvió un archivo PNG')
        image.verify()
        safe_suffix = ''.join(ch for ch in str(output_suffix or '') if ch.isalnum() or ch in '_-')
        filename = 'article_%s%s.png' % (article['id'], ('_' + safe_suffix) if safe_suffix else '')
        output = RENDER_DIR / filename
        temp = output.with_suffix('.tmp')
        temp.write_bytes(response.content)
        temp.replace(output)
    except (requests.RequestException, OSError) as exc:
        raise ValueError('No se pudo descargar o validar el PNG de Canva') from exc
    if mark_main:
        db.exec_('UPDATE articles SET render_path=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                 (str(output), article['id']))
        db.exec_('UPDATE canva_designs SET exported=1,updated_at=CURRENT_TIMESTAMP WHERE article_id=?',
                 (article['id'],))
    return str(output)

def export_design(article):
    if not ready() or not connected():
        raise ValueError('Conecta Canva antes de exportar el diseño')
    design = db.row('SELECT * FROM canva_designs WHERE article_id=?', (article['id'],))
    if not design:
        raise ValueError('Primero crea el diseño con la plantilla de Canva')
    if design.get('content_hash') != _content_hash(article):
        raise ValueError('La noticia o la foto cambió; vuelve a crear el diseño de Canva')
    db.exec_('UPDATE canva_designs SET exported=0 WHERE article_id=?', (article['id'],))
    db.exec_('UPDATE articles SET render_path=NULL,status=CASE WHEN status="approved" THEN "review_ready" ELSE status END WHERE id=?', (article['id'],))
    _export_png(article, design['design_id'], page=_page_for(article))
    return {'url': design['url'], 'design_id': design['design_id'], 'exported': True}


def create_design(article, store=True, title_suffix='', output_suffix=None, strict=True):
    if not ready() or not connected():
        raise ValueError('Conecta tu cuenta de Canva desde Ajustes')
    photo = Path(article.get('image_local') or '')
    if not photo.is_file():
        raise ValueError('Elige primero una foto para la noticia')
    from . import brands
    brand = brands.settings(article.get('brand'))
    texts = design_fields(article, strict)
    template_id = texts['template']
    if not template_id:
        raise ValueError('Falta la plantilla de Canva de %s. Pégala en Ajustes → Medios.' % brand['name'])
    schema = _api('GET', '/brand-templates/' + template_id + '/dataset').get('dataset', {})
    expected = {'HEADLINE': 'text', 'SUMMARY': 'text', 'PHOTO': 'image'}
    if _is_default(article):
        expected['SECTION'] = 'text'
    missing = [key for key, kind in expected.items() if schema.get(key, {}).get('type') != kind]
    if missing:
        raise ValueError('A la plantilla de Canva de %s le faltan los campos de datos: %s. En Canva, en la plantilla, '
                         'pon esos nombres a los cuadros de texto y a la foto (Aplicaciones → Autocompletar).' % (brand['name'], ', '.join(missing)))
    data = photo.read_bytes()
    if len(data) > 20_000_000:
        raise ValueError('La fotografía supera 20 MB')
    suffix = ''.join(ch for ch in str(output_suffix or '') if ch.isalnum() or ch in '_-')
    filename = re.sub(r'\W+', '', brand['name']) + '-' + str(article['id']) + (('-' + suffix) if suffix else '') + photo.suffix
    metadata = json.dumps({'name_base64': base64.b64encode(filename.encode()).decode()})
    upload = _api('POST', '/asset-uploads', data=data,
                  headers={'Content-Type': 'application/octet-stream', 'Asset-Upload-Metadata': metadata})
    done = _wait('/asset-uploads/' + upload['job']['id'], upload)
    asset = done.get('asset') or (done.get('result') or {}).get('asset') or {}
    if not asset.get('id'):
        raise ValueError('Canva no devolvió la fotografía cargada')
    fields = {
        'HEADLINE': {'type': 'text', 'text': texts['HEADLINE']},
        'SUMMARY': {'type': 'text', 'text': texts['SUMMARY']},
        'SECTION': {'type': 'text', 'text': texts['SECTION']},
        'PHOTO': {'type': 'image', 'asset_id': asset['id']},
    }
    if schema.get('SECTION', {}).get('type') != 'text' or not texts['SECTION']:
        fields.pop('SECTION')  # plantilla sin cuadro de sección
    name = brand['name'] + ' · ' + (article.get('headline') or '')[:100]
    if title_suffix:
        name += ' · ' + str(title_suffix)[:40]
    job = _api('POST', '/autofills', json={'type': 'create_from_brand_template',
                    'brand_template_id': template_id,
                    'title': name, 'data': fields})
    done = _wait('/autofills/' + job['job']['id'], job)
    design = _design_from_job(done)
    if not design.get('id'):
        raise ValueError('Canva terminó el trabajo pero no devolvió el diseño (respuesta: %s)' % json.dumps(done, ensure_ascii=False)[:200])
    # design.url es el enlace estable de edición; urls.edit_url caduca a los 30 días.
    url = design.get('url') or (design.get('urls') or {}).get('edit_url') or (design.get('urls') or {}).get('view_url') \
        or 'https://www.canva.com/design/%s/edit' % design['id']
    output = {'url': url, 'design_id': design.get('id'), 'exported': False}
    if store:
        db.exec_('INSERT OR REPLACE INTO canva_designs(article_id,design_id,url,exported,content_hash,updated_at) VALUES(?,?,?,0,?,CURRENT_TIMESTAMP)',
                 (article['id'], design['id'], url, _content_hash(article)))
        db.exec_('UPDATE articles SET render_path=NULL,status=CASE WHEN status="approved" THEN "review_ready" ELSE status END WHERE id=?', (article['id'],))
    try:
        _export_png(article, design['id'], output_suffix=output_suffix, mark_main=store, page=texts['page'])
        output['exported'] = True
    except (ValueError, requests.RequestException, KeyError, OSError) as exc:
        output['export_error'] = str(exc)
    return output


def create_carousel_designs(article, slides, selected_photos):
    from . import photos as photo_service

    if not 3 <= len(slides) <= 6:
        raise ValueError('El carrusel debe tener entre tres y seis diapositivas')
    if not 1 <= len(selected_photos) <= 6:
        raise ValueError('Selecciona entre una y seis fotografías autorizadas')
    if len(selected_photos) < len(slides):
        selected_photos = [selected_photos[index % len(selected_photos)] for index in range(len(slides))]
    elif len(selected_photos) > len(slides):
        selected_photos = selected_photos[:len(slides)]
    try:
        allowed = json.loads(article.get('image_candidates_json') or '[]')
    except Exception:
        allowed = []

    for old in RENDER_DIR.glob('article_%s_carousel_*.png' % article['id']):
        try:
            old.unlink()
        except OSError:
            pass
    db.exec_('DELETE FROM carousel_designs WHERE article_id=?', (article['id'],))

    results = []
    for index, (slide, photo) in enumerate(zip(slides, selected_photos), start=1):
        try:
            local_path = photo_service.download_image(photo['url'])
        except Exception as exc:
            raise ValueError('No se pudo descargar la foto de la diapositiva %s' % index) from exc
        page = dict(article)
        page.update({
            'headline': slide.get('title') or '',
            'image_headline': '',
            'graphic_summary': slide.get('text') or '',
            'subtitle': slide.get('text') or '',
            'image_url': photo.get('url') or '',
            'image_source': photo.get('source') or '',
            'image_license': photo.get('license') or '',
            'image_local': local_path,
            'image_candidates_json': json.dumps([photo], ensure_ascii=False),
        })
        suffix = 'carousel_%s' % index
        result = create_design(
            page, store=False, title_suffix='Carrusel %s/%s' % (index, len(slides)),
            output_suffix=suffix, strict=False
        )
        if not result.get('exported'):
            raise ValueError(result.get('export_error') or 'Canva no exportó la diapositiva %s' % index)
        db.exec_(
            '''INSERT OR REPLACE INTO carousel_designs
               (article_id,slide_index,design_id,url,exported,content_hash,image_url,image_source,image_license,updated_at)
               VALUES(?,?,?,?,1,?,?,?,?,CURRENT_TIMESTAMP)''',
            (article['id'], index, result['design_id'], result['url'], _content_hash(page),
             photo.get('url') or '', photo.get('source') or '', photo.get('license') or '')
        )
        results.append({
            'index': index, 'title': slide.get('title') or '',
            'text': slide.get('text') or '', 'url': result['url'],
            'exported': True, 'image_source': photo.get('source') or '',
            'image_license': photo.get('license') or '',
            'download_path': '/api/articles/%s/carousel/slide/%s' % (article['id'], index),
        })
    first_url = results[0]['url'] if results else None
    return {
        'slides': results,
        'url': first_url,
        'design_url': first_url,
        'download_url': '/api/articles/%s/carousel/download' % article['id'],
    }
