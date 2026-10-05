"""Publicación en Instagram, Facebook y TikTok.

Solo se publica cuando el usuario pulsa «Publicar». Cada red se conecta una vez desde Ajustes
(inicio de sesión oficial de Meta y de TikTok). Las claves y tokens se quedan en el servidor.

- Facebook: foto (o varias, si hay carrusel) en la página, con el texto de la noticia.
- Instagram (cuenta profesional unida a la página): foto o carrusel con el texto como pie.
- TikTok: publicación de fotos (carrusel de fotos) con título y descripción.

Las redes descargan la imagen desde una dirección pública temporal de este servidor
(/p/<código>.jpg), que solo se crea en el momento de publicar.
"""
import json
import os
import re
import secrets
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode

import requests
from PIL import Image

from . import db
from .config import PUBLIC_BASE_URL, RENDER_DIR

META_APP_ID = os.getenv('META_APP_ID', '').strip()
META_APP_SECRET = os.getenv('META_APP_SECRET', '').strip()
META_CONFIG_ID = os.getenv('META_CONFIG_ID', '').strip()
META_GRAPH_VERSION = os.getenv('META_GRAPH_VERSION', 'v21.0').strip()
# Alternativa sin botón: token de página permanente puesto a mano en Railway
META_PAGE_ID = os.getenv('META_PAGE_ID', '').strip()
META_PAGE_TOKEN = os.getenv('META_PAGE_TOKEN', '').strip()
INSTAGRAM_USER_ID = os.getenv('INSTAGRAM_USER_ID', '').strip()
TIKTOK_CLIENT_KEY = os.getenv('TIKTOK_CLIENT_KEY', '').strip()
TIKTOK_CLIENT_SECRET = os.getenv('TIKTOK_CLIENT_SECRET', '').strip()
TIKTOK_VERIFY_FILE = os.getenv('TIKTOK_VERIFY_FILE', '').strip()
TIKTOK_VERIFY_CONTENT = os.getenv('TIKTOK_VERIFY_CONTENT', '').strip()
TIKTOK_PRIVACY = os.getenv('TIKTOK_PRIVACY', 'PUBLIC_TO_EVERYONE').strip()
SOCIAL_HASHTAGS = os.getenv('SOCIAL_HASHTAGS', '#LaLínea #LaLíneaDeLaConcepción #InfoLinense').strip()

GRAPH = 'https://graph.facebook.com/' + META_GRAPH_VERSION
TIKTOK_API = 'https://open.tiktokapis.com/v2'
META_SCOPES = ','.join(['pages_show_list', 'pages_read_engagement', 'pages_manage_posts', 'business_management',
                        'instagram_basic', 'instagram_content_publish'])
TIKTOK_SCOPES = os.getenv('TIKTOK_SCOPES', 'user.info.basic,video.publish').replace(' ', '')
NETWORKS = ('instagram', 'facebook', 'tiktok')
NAMES = {'instagram': 'Instagram', 'facebook': 'Facebook', 'tiktok': 'TikTok'}


class SocialError(Exception):
    pass


def init_tables():
    db.exec_('''CREATE TABLE IF NOT EXISTS social_accounts(
        network TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    db.exec_('''CREATE TABLE IF NOT EXISTS social_oauth_state(
        state TEXT PRIMARY KEY, network TEXT NOT NULL, expires_at INTEGER NOT NULL)''')
    db.exec_('''CREATE TABLE IF NOT EXISTS social_posts(
        article_id INTEGER NOT NULL, network TEXT NOT NULL, status TEXT NOT NULL, remote_id TEXT, url TEXT,
        message TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(article_id, network))''')
    db.exec_('''CREATE TABLE IF NOT EXISTS public_media(
        token TEXT PRIMARY KEY, path TEXT NOT NULL, expires_at INTEGER NOT NULL)''')


# ---------- cuentas guardadas ----------

def _account(network):
    row = db.row('SELECT data FROM social_accounts WHERE network=?', (network,))
    try:
        return json.loads(row['data']) if row else None
    except ValueError:
        return None


def _save_account(network, data):
    db.exec_('INSERT OR REPLACE INTO social_accounts(network,data,updated_at) VALUES(?,?,CURRENT_TIMESTAMP)',
             (network, json.dumps(data, ensure_ascii=False)))


def disconnect(network):
    if network == 'meta':
        db.exec_("DELETE FROM social_accounts WHERE network IN ('meta')")
    else:
        db.exec_('DELETE FROM social_accounts WHERE network=?', (network,))


def _meta():
    """Página de Facebook e Instagram en uso: la conectada con el botón o la puesta a mano en Railway."""
    acc = _account('meta') or {}
    page = acc.get('page') or {}
    if page.get('access_token'):
        return page
    if META_PAGE_ID and META_PAGE_TOKEN:
        return {'id': META_PAGE_ID, 'name': '', 'access_token': META_PAGE_TOKEN,
                'instagram_id': INSTAGRAM_USER_ID, 'instagram_username': ''}
    return {}


def status():
    """Estado de cada red para Ajustes y para la pantalla de Publicar."""
    meta = _meta()
    acc = _account('meta') or {}
    tiktok = _account('tiktok') or {}
    return {
        'public_url_ok': PUBLIC_BASE_URL.startswith('https://'),
        'meta_configured': bool(META_APP_ID and META_APP_SECRET and PUBLIC_BASE_URL) or bool(META_PAGE_ID and META_PAGE_TOKEN),
        'meta_login': bool(META_APP_ID and META_APP_SECRET and PUBLIC_BASE_URL),
        'meta_callback': PUBLIC_BASE_URL + '/api/networks/meta/callback' if PUBLIC_BASE_URL else '',
        'facebook': {'connected': bool(meta.get('access_token')), 'name': meta.get('name') or ''},
        'instagram': {'connected': bool(meta.get('access_token') and meta.get('instagram_id')),
                      'name': meta.get('instagram_username') or ''},
        'pages': [{'id': p['id'], 'name': p.get('name') or '', 'instagram': p.get('instagram_username') or ''}
                  for p in acc.get('pages', [])],
        'tiktok_configured': bool(TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET and PUBLIC_BASE_URL),
        'tiktok_callback': PUBLIC_BASE_URL + '/api/networks/tiktok/callback' if PUBLIC_BASE_URL else '',
        'tiktok': {'connected': bool(tiktok.get('refresh_token')), 'name': tiktok.get('name') or ''},
        'tiktok_url_prefix': PUBLIC_BASE_URL + '/p/' if PUBLIC_BASE_URL else '',
        'privacy_url': PUBLIC_BASE_URL + '/privacidad' if PUBLIC_BASE_URL else '',
        'terms_url': PUBLIC_BASE_URL + '/terminos' if PUBLIC_BASE_URL else '',
    }


def connected_networks():
    s = status()
    return [n for n in NETWORKS if s[n]['connected']]


# ---------- inicio de sesión (OAuth) ----------

def _new_state(network):
    state = secrets.token_urlsafe(32)
    db.exec_('DELETE FROM social_oauth_state WHERE expires_at<?', (int(time.time()),))
    db.exec_('INSERT INTO social_oauth_state VALUES(?,?,?)', (state, network, int(time.time()) + 900))
    return state


def _check_state(network, state):
    row = db.row('SELECT network,expires_at FROM social_oauth_state WHERE state=?', (state or '',))
    db.exec_('DELETE FROM social_oauth_state WHERE state=?', (state or '',))
    if not row or row['network'] != network or row['expires_at'] < int(time.time()):
        raise SocialError('La autorización caducó o no se inició desde InfoLinense Desk. Vuelve a pulsar «Conectar».')


def meta_login_url():
    if not (META_APP_ID and META_APP_SECRET and PUBLIC_BASE_URL):
        raise SocialError('Faltan META_APP_ID, META_APP_SECRET o PUBLIC_BASE_URL en Railway')
    params = {'client_id': META_APP_ID, 'redirect_uri': status()['meta_callback'], 'state': _new_state('meta'),
              'response_type': 'code'}
    if META_CONFIG_ID:  # «Inicio de sesión con Facebook para empresas»: los permisos van en la configuración
        params['config_id'] = META_CONFIG_ID
    else:
        params['scope'] = META_SCOPES
    return 'https://www.facebook.com/%s/dialog/oauth?' % META_GRAPH_VERSION + urlencode(params)


def _graph(method, path, **kw):
    try:
        r = requests.request(method, GRAPH + path, timeout=60, **kw)
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise SocialError('Meta no responde: %s' % str(exc)[:150])
    if not r.ok or (isinstance(data, dict) and data.get('error')):
        err = (data or {}).get('error') or {}
        raise SocialError('Meta: %s' % (err.get('error_user_msg') or err.get('message') or 'HTTP %s' % r.status_code)[:300])
    return data


def meta_complete(code, state):
    _check_state('meta', state)
    short = _graph('GET', '/oauth/access_token', params={
        'client_id': META_APP_ID, 'client_secret': META_APP_SECRET,
        'redirect_uri': status()['meta_callback'], 'code': code})['access_token']
    return meta_from_user_token(short)


def meta_from_user_token(short):
    """A partir de un token de usuario (del inicio de sesión o pegado desde el Explorador de la API Graph)
    obtiene tokens de página que no caducan y guarda la página y su Instagram."""
    short = (short or '').strip()
    if len(short) < 20:
        raise SocialError('Ese token no parece válido. Cópialo entero desde el Explorador de la API Graph.')
    if not (META_APP_ID and META_APP_SECRET):
        raise SocialError('Faltan META_APP_ID y META_APP_SECRET en Railway')
    long_user = _graph('GET', '/oauth/access_token', params={
        'grant_type': 'fb_exchange_token', 'client_id': META_APP_ID, 'client_secret': META_APP_SECRET,
        'fb_exchange_token': short})['access_token']
    # Los tokens de página obtenidos con un token de usuario de larga duración no caducan.
    data = _graph('GET', '/me/accounts', params={
        'access_token': long_user, 'limit': 100,
        'fields': 'id,name,access_token,instagram_business_account{id,username}'})
    pages = []
    for p in data.get('data') or []:
        ig = p.get('instagram_business_account') or {}
        pages.append({'id': p['id'], 'name': p.get('name') or '', 'access_token': p['access_token'],
                      'instagram_id': ig.get('id') or '', 'instagram_username': ig.get('username') or ''})
    if not pages:
        raise SocialError('Tu usuario de Facebook no administra ninguna página o no diste permiso a la página.')
    chosen = next((p for p in pages if p['id'] == META_PAGE_ID), None) \
        or next((p for p in pages if p['instagram_id']), None) or pages[0]
    _save_account('meta', {'pages': pages, 'page': chosen})
    return chosen


def meta_choose_page(page_id):
    acc = _account('meta') or {}
    page = next((p for p in acc.get('pages', []) if p['id'] == str(page_id)), None)
    if not page:
        raise SocialError('Esa página no está entre las autorizadas')
    acc['page'] = page
    _save_account('meta', acc)
    return page


def tiktok_login_url():
    if not (TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET and PUBLIC_BASE_URL):
        raise SocialError('Faltan TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET o PUBLIC_BASE_URL en Railway')
    return 'https://www.tiktok.com/v2/auth/authorize/?' + urlencode({
        'client_key': TIKTOK_CLIENT_KEY, 'scope': TIKTOK_SCOPES, 'response_type': 'code',
        'redirect_uri': status()['tiktok_callback'], 'state': _new_state('tiktok')})


def _tiktok_token(form):
    try:
        r = requests.post(TIKTOK_API + '/oauth/token/', data={
            'client_key': TIKTOK_CLIENT_KEY, 'client_secret': TIKTOK_CLIENT_SECRET, **form},
            headers={'Content-Type': 'application/x-www-form-urlencoded'}, timeout=30)
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise SocialError('TikTok no responde: %s' % str(exc)[:150])
    if not data.get('access_token'):
        raise SocialError('TikTok: %s' % (data.get('error_description') or data.get('error') or 'no devolvió el acceso'))
    now = int(time.time())
    acc = _account('tiktok') or {}
    acc.update({'access_token': data['access_token'], 'refresh_token': data.get('refresh_token') or acc.get('refresh_token'),
                'expires_at': now + int(data.get('expires_in') or 86400) - 120,
                'refresh_expires_at': now + int(data.get('refresh_expires_in') or 0), 'open_id': data.get('open_id') or acc.get('open_id')})
    _save_account('tiktok', acc)
    return acc


def tiktok_complete(code, state):
    _check_state('tiktok', state)
    acc = _tiktok_token({'code': code, 'grant_type': 'authorization_code', 'redirect_uri': status()['tiktok_callback']})
    try:
        info = requests.get(TIKTOK_API + '/user/info/', params={'fields': 'display_name'},
                            headers={'Authorization': 'Bearer ' + acc['access_token']}, timeout=20).json()
        acc['name'] = ((info.get('data') or {}).get('user') or {}).get('display_name') or ''
        _save_account('tiktok', acc)
    except Exception:
        pass
    return acc


def _tiktok_access():
    acc = _account('tiktok')
    if not acc or not acc.get('refresh_token'):
        raise SocialError('TikTok no está conectado')
    if acc.get('expires_at', 0) > time.time():
        return acc['access_token']
    return _tiktok_token({'grant_type': 'refresh_token', 'refresh_token': acc['refresh_token']})['access_token']


def _tiktok(path, body):
    try:
        r = requests.post(TIKTOK_API + path, json=body, timeout=60, headers={
            'Authorization': 'Bearer ' + _tiktok_access(), 'Content-Type': 'application/json; charset=UTF-8'})
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise SocialError('TikTok no responde: %s' % str(exc)[:150])
    err = data.get('error') or {}
    if err.get('code') not in (None, '', 'ok'):
        raise SocialError('TikTok: %s' % (err.get('message') or err.get('code'))[:300])
    return data.get('data') or {}


# ---------- imágenes públicas para las redes ----------

def public_image(path):
    """Copia en JPG la imagen y devuelve una URL pública temporal (las redes la descargan desde aquí)."""
    src = Path(path or '')
    if not src.is_file():
        raise SocialError('Falta la imagen final. Crea la imagen en Canva antes de publicar en redes.')
    if not PUBLIC_BASE_URL.startswith('https://'):
        raise SocialError('Falta PUBLIC_BASE_URL (https://…) en Railway: las redes necesitan descargar la imagen')
    token = secrets.token_urlsafe(24)
    out = RENDER_DIR / ('public_%s.jpg' % token)
    im = Image.open(src).convert('RGB')
    buf = BytesIO()
    im.save(buf, 'JPEG', quality=92)
    out.write_bytes(buf.getvalue())
    db.exec_('DELETE FROM public_media WHERE expires_at<?', (int(time.time()),))
    db.exec_('INSERT INTO public_media VALUES(?,?,?)', (token, str(out), int(time.time()) + 7 * 86400))
    return PUBLIC_BASE_URL + '/p/' + token + '.jpg'


def public_file(token):
    row = db.row('SELECT path,expires_at FROM public_media WHERE token=?', (token,))
    if not row or row['expires_at'] < time.time() or not Path(row['path']).is_file():
        return None
    return row['path']


def article_images(article):
    """Imagen de la noticia; si hay carrusel exportado, todas sus diapositivas en orden."""
    slides = db.rows('SELECT slide_index FROM carousel_designs WHERE article_id=? AND exported=1 ORDER BY slide_index',
                     (article['id'],))
    paths = [RENDER_DIR / ('article_%s_carousel_%s.png' % (article['id'], s['slide_index'])) for s in slides]
    paths = [str(p) for p in paths if p.is_file()]
    if len(paths) >= 2:
        main = article.get('render_path') or ''
        return ([main] if main and Path(main).is_file() else []) + paths[:9]
    if article.get('render_path') and Path(article['render_path']).is_file():
        return [article['render_path']]
    raise SocialError('Falta la imagen final. Crea la imagen en Canva antes de publicar en redes.')


# ---------- textos ----------

def caption(article, limit=2200):
    parts = [article.get('headline') or '', article.get('subtitle') or '', article.get('body') or '']
    text = '\n\n'.join(p.strip() for p in parts if p and p.strip())
    tags = SOCIAL_HASHTAGS
    room = limit - (len(tags) + 2 if tags else 0)
    if len(text) > room:
        cut = text[:room - 1]
        end = max(cut.rfind('. '), cut.rfind('\n'))
        text = (cut[:end + 1] if end > room * 0.6 else cut.rsplit(' ', 1)[0]).rstrip() + ('' if end > room * 0.6 else '…')
    return text + ('\n\n' + tags if tags else '')


# ---------- publicar ----------

def _facebook(article, urls):
    page = _meta()
    if not page.get('access_token'):
        raise SocialError('Facebook no está conectado')
    token, pid = page['access_token'], page['id']
    message = caption(article, 60000)
    if len(urls) == 1:
        d = _graph('POST', '/%s/photos' % pid, data={'url': urls[0], 'message': message, 'published': 'true',
                                                     'access_token': token})
        post_id = d.get('post_id') or d.get('id')
    else:
        ids = [_graph('POST', '/%s/photos' % pid, data={'url': u, 'published': 'false', 'access_token': token})['id']
               for u in urls]
        form = {'message': message, 'access_token': token}
        for i, mid in enumerate(ids):
            form['attached_media[%d]' % i] = json.dumps({'media_fbid': mid})
        post_id = _graph('POST', '/%s/feed' % pid, data=form)['id']
    return post_id, 'https://www.facebook.com/' + str(post_id)


def _ig_wait(container, token, seconds=90):
    end = time.time() + seconds
    while time.time() < end:
        st = _graph('GET', '/' + container, params={'fields': 'status_code,status', 'access_token': token})
        code = st.get('status_code')
        if code == 'FINISHED':
            return
        if code in ('ERROR', 'EXPIRED'):
            raise SocialError('Instagram no pudo procesar la imagen (%s)' % (st.get('status') or code))
        time.sleep(3)
    raise SocialError('Instagram tardó demasiado en procesar la imagen; inténtalo de nuevo')


def _instagram(article, urls):
    page = _meta()
    if not (page.get('access_token') and page.get('instagram_id')):
        raise SocialError('Instagram no está conectado (la cuenta debe ser profesional y estar unida a la página de Facebook)')
    token, ig = page['access_token'], page['instagram_id']
    text = caption(article, 2200)
    if len(urls) == 1:
        container = _graph('POST', '/%s/media' % ig, data={'image_url': urls[0], 'caption': text, 'access_token': token})['id']
    else:
        children = []
        for u in urls[:10]:
            child = _graph('POST', '/%s/media' % ig, data={'image_url': u, 'is_carousel_item': 'true', 'access_token': token})['id']
            _ig_wait(child, token)
            children.append(child)
        container = _graph('POST', '/%s/media' % ig, data={'media_type': 'CAROUSEL', 'children': ','.join(children),
                                                           'caption': text, 'access_token': token})['id']
    _ig_wait(container, token)
    media = _graph('POST', '/%s/media_publish' % ig, data={'creation_id': container, 'access_token': token})['id']
    try:
        link = _graph('GET', '/' + media, params={'fields': 'permalink', 'access_token': token}).get('permalink') or ''
    except SocialError:
        link = ''
    return media, link


def _tiktok_post(article, urls):
    info = _tiktok('/post/publish/creator_info/query/', {})
    options = info.get('privacy_level_options') or ['SELF_ONLY']
    privacy = TIKTOK_PRIVACY if TIKTOK_PRIVACY in options else options[0]
    title = re.sub(r'\s+', ' ', article.get('headline') or 'InfoLinense')[:90]
    data = _tiktok('/post/publish/content/init/', {
        'post_info': {'title': title, 'description': caption(article, 4000), 'privacy_level': privacy,
                      'disable_comment': False, 'auto_add_music': True},
        'source_info': {'source': 'PULL_FROM_URL', 'photo_cover_index': 0, 'photo_images': urls[:35]},
        'post_mode': 'DIRECT_POST', 'media_type': 'PHOTO'})
    publish_id = data.get('publish_id') or ''
    note = '' if privacy == 'PUBLIC_TO_EVERYONE' else ' (privada: TikTok solo permite publicar en público cuando aprueba tu app)'
    return publish_id, '', note


def publish(network, article):
    """Publica en una red. No repite si ya se publicó. Devuelve el resultado y lo guarda."""
    if network not in NETWORKS:
        raise SocialError('Red desconocida')
    done = db.row("SELECT * FROM social_posts WHERE article_id=? AND network=? AND status='published'", (article['id'], network))
    if done:
        return {'network': network, 'ok': True, 'url': done.get('url') or '', 'message': 'Ya estaba publicada'}
    note = ''
    try:
        urls = [public_image(p) for p in article_images(article)]
        if network == 'facebook':
            remote, url = _facebook(article, urls)
        elif network == 'instagram':
            remote, url = _instagram(article, urls)
        else:
            remote, url, note = _tiktok_post(article, urls)
    except SocialError as exc:
        db.exec_('INSERT OR REPLACE INTO social_posts(article_id,network,status,message,updated_at) VALUES(?,?,?,?,CURRENT_TIMESTAMP)',
                 (article['id'], network, 'error', str(exc)[:400]))
        return {'network': network, 'ok': False, 'message': str(exc)}
    except Exception as exc:  # nunca dejar a medias la publicación en otras redes
        db.exec_('INSERT OR REPLACE INTO social_posts(article_id,network,status,message,updated_at) VALUES(?,?,?,?,CURRENT_TIMESTAMP)',
                 (article['id'], network, 'error', 'Error inesperado: %s' % str(exc)[:300]))
        return {'network': network, 'ok': False, 'message': 'Error inesperado: %s' % str(exc)[:200]}
    message = 'Publicada en %s%s' % (NAMES[network], note)
    db.exec_('INSERT OR REPLACE INTO social_posts(article_id,network,status,remote_id,url,message,updated_at) VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)',
             (article['id'], network, 'published', str(remote), url, message))
    db.log('publish', 'Noticia %s publicada en %s' % (article['id'], NAMES[network]))
    return {'network': network, 'ok': True, 'url': url, 'message': message}


def posts_for(article_ids):
    if not article_ids:
        return {}
    marks = ','.join('?' * len(article_ids))
    out = {}
    for r in db.rows('SELECT article_id,network,status,url,message FROM social_posts WHERE article_id IN (%s)' % marks,
                     tuple(article_ids)):
        out.setdefault(r['article_id'], {})[r['network']] = {'status': r['status'], 'url': r.get('url') or '',
                                                              'message': r.get('message') or ''}
    return out
