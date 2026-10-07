"""Canva: conexión OAuth y carrusel desde la plantilla de marca de Área (autocompletar + exportar PNG).

PLANTILLA POR TIPOS (la recomendada): una página por tipo de diapositiva, con los cuadros nombrados TIPO_CAMPO
(PORTADA_TITULAR, LISTA_TITULO, LISTA_PUNTO1…). Para tener dos diapositivas del mismo tipo se duplica la página y se numera
el tipo: LISTA2_TITULO… La app rellena las páginas que usa el carrusel y exporta solo esas, en el orden del carrusel.
Los gráficos (barras, flechas, cajas, degradados) son fijos en la plantilla; la app solo pone textos y fotos.

PLANTILLA NUMERADA (antigua): TITULO_1, TEXTO_1, FOTO_1 … por número de diapositiva.
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



# ---------------------------------------------------------------- plantilla por tipos

# tipo de diapositiva → prefijo en Canva y campos (CAMPO → de dónde sale)
TYPES = {
    'portada': ('PORTADA', ['TITULAR', 'ENTRADILLA', 'FOTO']),
    'lista': ('LISTA', ['TITULO', 'SUBTITULO', 'PUNTO1', 'PUNTO2', 'PUNTO3', 'PUNTO4', 'FOTO']),
    'caja': ('CAJA', ['TITULO', 'SUBTITULO', 'LISTA', 'FOTO']),
    'flujo': ('FLUJO', ['TITULO', 'SUBTITULO', 'PASO1', 'PASO2', 'PASO3', 'FRASE1', 'FRASE2', 'FRASE3', 'FOTO']),
    'cifra_lista': ('CIFRALISTA', ['TITULO', 'SUBTITULO', 'PUNTO1', 'PUNTO2', 'PUNTO3', 'CIFRA', 'FOTO']),
    'ficha': ('FICHA', ['TITULO', 'TEXTO', 'ESTADO', 'CIFRA', 'FOTO']),
    'pregunta': ('PREGUNTA', ['PREGUNTA', 'RESPUESTA']),
    'anotada': ('ANOTADA', ['CONTEXTO', 'TITULO', 'NOTA1', 'NOTA2', 'NOTA3', 'FOTO']),
    'mapa': ('MAPA', ['TITULO', 'TEXTO', 'FOTO', 'IMAGEN']),
    'cifra': ('CIFRA', ['ANTETITULO', 'CIFRA', 'ETIQUETA', 'TEXTO', 'FOTO']),
    'tarjetas': ('TARJETAS', ['ANTETITULO', 'TITULO'] + ['T%s_%s' % (n, f) for n in (1, 2, 3) for f in ('ROTULO', 'CIFRA', 'TEXTO')] + ['FOTO']),
    'mosaico': ('MOSAICO', ['ANTETITULO'] + ['M%s_%s' % (n, f) for n in (1, 2, 3, 4, 5) for f in ('ROTULO', 'TEXTO', 'FOTO')]),
    'documento': ('DOCUMENTO', ['ANTETITULO', 'TITULO', 'TEXTO', 'DOCUMENTO']),
    'calles': ('CALLES', ['TITULO', 'SUBTITULO'] + ['%s%s' % (f, n) for n in range(1, 7) for f in ('CALLE', 'OBRA')] + ['FOTO']),
}
PREFIXES = sorted(((v[0], k) for k, v in TYPES.items()), key=lambda x: -len(x[0]))
IMAGE_FIELDS = re.compile(r'^(FOTO|IMAGEN|DOCUMENTO|M\d_FOTO)$')


def _up(name):
    t = unicodedata.normalize('NFD', str(name or '').upper())
    t = ''.join(c for c in t if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^A-Z0-9]+', '_', t).strip('_')


def parse_typed(name):
    """«LISTA2_PUNTO3» → ('lista', 2, 'PUNTO3'); si no sigue la convención, None."""
    n = _up(name)
    for prefix, layout in PREFIXES:
        m = re.match(r'^%s(\d*)_(.+)$' % prefix, n)
        if m and m.group(2) in TYPES[layout][1]:
            return layout, int(m.group(1) or 1), m.group(2)
    return None


def _plain(t):
    return re.sub(r'==|\+\+|\*\*', '', str(t or '')).strip()


def slide_value(slide, field):
    s, at = slide, lambda lst, i: _plain(lst[i]) if i < len(lst) else ''
    bullets, chips, cards = s.get('bullets') or [], s.get('chips') or [], s.get('cards') or []
    m = re.match(r'^(PUNTO|NOTA|FRASE|PASO)(\d)$', field)
    if m:
        return at(chips if m.group(1) == 'PASO' else bullets, int(m.group(2)) - 1)
    m = re.match(r'^(CALLE|OBRA)(\d)$', field) or re.match(r'^[TM](\d)_(ROTULO|CIFRA|TEXTO)$', field)
    if m:
        if field.startswith(('CALLE', 'OBRA')):
            i, key = int(m.group(2)) - 1, 'label' if m.group(1) == 'CALLE' else 'text'
        else:
            i, key = int(m.group(1)) - 1, {'ROTULO': 'label', 'CIFRA': 'figure', 'TEXTO': 'text'}[m.group(2)]
        return _plain(cards[i].get(key)) if i < len(cards) else ''
    if field == 'LISTA':
        return '\n'.join(_plain(b) for b in bullets)
    if field == 'CIFRA':
        fig = _plain(s.get('figure'))
        return (fig + ' ' + _plain(s.get('figure_label'))).strip() if s.get('layout') == 'cifra_lista' else fig
    key = {'TITULAR': 'title', 'TITULO': 'title', 'PREGUNTA': 'title', 'ENTRADILLA': 'text', 'SUBTITULO': 'text', 'TEXTO': 'text',
           'RESPUESTA': 'text', 'CONTEXTO': 'kicker', 'ANTETITULO': 'kicker', 'ESTADO': 'status', 'ETIQUETA': 'figure_label'}.get(field)
    return _plain(s.get(key)) if key else ''


def slide_image(images, field):
    m = re.match(r'^M(\d)_FOTO$', field)
    i = int(m.group(1)) - 1 if m else (1 if field == 'IMAGEN' else 0)
    if not images:
        return None
    return images[i] if i < len(images) else (images[0] if field != 'IMAGEN' else images[-1])


def typed_instances(fields):
    """Instancias de la plantilla en el orden en que aparecen: [('portada',1), ('lista',1), ('lista',2)…]."""
    out = []
    for f in fields:
        p = parse_typed(f['name'])
        if p and (p[0], p[1]) not in out:
            out.append((p[0], p[1]))
    return out


def inst_name(layout, n):
    return TYPES[layout][0] + ('' if n == 1 else str(n))


def page_order(fields):
    """Orden de páginas: el guardado en Ajustes o, si no, el de aparición de los campos."""
    saved = [_up(x) for x in (db.setting('canva_pages') or '').replace('\n', ',').split(',') if x.strip()]
    if saved:
        return saved
    return [inst_name(l, n) for l, n in typed_instances(fields)]


def plan_typed(fields, slides):
    """Qué página de la plantilla usa cada diapositiva. Devuelve ([(slide_index, layout, n, page)], [diapositivas sin página])."""
    available = typed_instances(fields)
    order = page_order(fields)
    used, plan, missing = set(), [], []
    for i, s in enumerate(slides):
        layout = s.get('layout') or 'lista'
        inst = next(((l, n) for l, n in available if l == layout and (l, n) not in used), None)
        if not inst or inst_name(*inst) not in order:
            missing.append(i + 1)
            continue
        used.add(inst)
        plan.append((i, inst[0], inst[1], order.index(inst_name(*inst)) + 1))
    return plan, missing


def create_typed(article, slides, slide_images, fields):
    plan, missing = plan_typed(fields, slides)
    if not plan:
        raise ValueError('La plantilla no tiene páginas para los tipos de este carrusel.')
    by_inst = {(l, n): i for i, l, n, _ in plan}
    data, uploads = {}, {}
    for f in fields:
        p = parse_typed(f['name'])
        if not p:
            continue
        layout, n, field = p
        i = by_inst.get((layout, n))
        if f['type'] == 'text':
            data[f['name']] = {'type': 'text', 'text': (slide_value(slides[i], field) if i is not None else '') or ' '}
        elif f['type'] == 'image' and i is not None:
            path = slide_image(slide_images[i], field)
            if path:
                if path not in uploads:
                    uploads[path] = _upload(path, 'Area-%s-%s%s' % (article['id'], len(uploads) + 1, Path(path).suffix))
                data[f['name']] = {'type': 'image', 'asset_id': uploads[path]}
    job = _api('POST', '/autofills', json={'type': 'create_from_brand_template', 'brand_template_id': template_id(),
                                          'title': 'Área · ' + (article.get('headline') or '')[:100], 'data': data})
    design = _design_from_job(_wait('/autofills/' + job['job']['id'], job))
    if not design.get('id'):
        raise ValueError('Canva no devolvió el diseño')
    url = design.get('url') or (design.get('urls') or {}).get('edit_url') or 'https://www.canva.com/design/%s/edit' % design['id']
    pages = [p for _, _, _, p in plan]
    db.exec_('UPDATE articles SET canva_design_id=?,canva_url=?,canva_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?',
             (design['id'], url, article['id']))
    db.set_setting('canva_pages_%s' % article['id'], json.dumps({'pages': pages, 'slides': [i + 1 for i, _, _, _ in plan]}))
    export_pages(article['id'], design['id'], pages, [i + 1 for i, _, _, _ in plan], url)
    return {'url': url, 'design_id': design['id'], 'pages': len(pages), 'missing_slides': missing}


def export_pages(article_id, design_id, pages, slide_numbers, url=''):
    for old in RENDER_DIR.glob('area_%s_*.png' % article_id):
        try:
            old.unlink()
        except OSError:
            pass
    db.exec_("DELETE FROM media WHERE article_id=? AND kind='canva'", (article_id,))
    paths = _export_pages(design_id, article_id, pages)
    for p, n in zip(paths, slide_numbers):
        db.exec_("INSERT INTO media(article_id,kind,url,local_path,caption,slide,selected) VALUES(?,?,?,?,?,?,1)",
                 (article_id, 'canva', url, p, 'Diapositiva %s (Canva)' % n, n))


def create_any(article, slides, slide_images):
    """Usa la plantilla por tipos si la plantilla sigue esa convención; si no, la numerada."""
    if not ready() or not connected():
        raise ValueError('Conecta Canva desde Ajustes')
    fields = template_fields()
    if any(parse_typed(f['name']) for f in fields):
        return create_typed(article, slides, slide_images, fields)
    flat = [(imgs[0] if imgs else None) for imgs in slide_images]
    flat = [p for p in flat if p] or [p for imgs in slide_images for p in imgs]
    if not flat:
        raise ValueError('Asigna al menos una foto al carrusel')
    return create_carousel(article, slides, [flat[i % len(flat)] for i in range(len(slides))])


def field_guide():
    """Nombres de cuadros que espera la plantilla por tipos (para Ajustes)."""
    return [{'layout': k, 'prefix': v[0], 'fields': ['%s_%s' % (v[0], f) for f in v[1]]} for k, v in TYPES.items()]
