"""Read only explicitly approved Page sources; never infer authorship from names/search."""
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs

from . import db, social_publish as meta

REQUIREMENTS = ('Hace falta un token de acceso de cada página oficial con pages_read_engagement. '
                'Conexión OAuth: META_APP_ID, META_APP_SECRET y PUBLIC_BASE_URL, pages_show_list '
                'y acceso asignado a cada página. Alternativa: META_PAGE_ID/META_PAGE_TOKEN o '
                'META_READER_PAGE_TOKENS (JSON de ID de página a token). Para páginas no administradas, '
                'Meta debe aprobar Page Public Content Access; conectar solo la página del medio no da acceso a las hermandades.')


def account(url):
    u = urlparse(url or '')
    if u.scheme != 'https' or u.hostname not in {'facebook.com', 'www.facebook.com', 'm.facebook.com'} or u.username or u.password:
        return ''
    parts = u.path.strip('/').split('/')
    if parts == ['profile.php']:
        value = parse_qs(u.query).get('id', [''])[0]
        return value if value.isdigit() else ''
    if len(parts) == 3 and parts[0] == 'pages' and parts[2].isdigit():
        return parts[2]
    if len(parts) == 1 and parts[0].lower() not in {'groups', 'search', 'watch', 'reel', 'share', 'posts', 'events', 'me'}:
        return parts[0].lower()
    return ''


def page_posts(url, now=None):
    wanted = account(url)
    if not wanted:
        raise meta.SocialError('La fuente debe ser una página de Facebook, no un perfil o grupo sin verificar.')
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=7)
    for p in meta._managed_pages():
        pid, token = str(p['id']), p['access_token']
        # category is a Page field: a personal profile must never qualify.
        if wanted.isdigit() and wanted != pid:
            continue
        try:
            page = meta._graph('GET', '/' + pid, params={'fields': 'id,name,category,link,username', 'access_token': token})
        except meta.SocialError:
            continue  # A revoked token for another page must not prevent reading this page.

        if str(page.get('id')) != pid or not page.get('category'):
            continue
        identities = {pid, str(page.get('username') or '').lower(), account(page.get('link') or '')} - {''}
        if wanted not in identities:
            continue
        items, seen, cursors = [], set(), set()
        params = {'fields': 'id,from,message,created_time,permalink_url,full_picture',
                  'since': int(since.timestamp()), 'until': int(now.timestamp()), 'limit': 100, 'access_token': token}
        while True:
            data = meta._graph('GET', '/' + pid + '/posts', params=params)
            for post in data.get('data') or []:
                post_id = str(post.get('id') or '')
                if str((post.get('from') or {}).get('id')) != pid or not post_id.startswith(pid + '_') or post_id in seen:
                    continue
                try:
                    date = datetime.fromisoformat(post.get('created_time', '').replace('Z', '+00:00'))
                    if date.tzinfo is None or not since <= date <= now:
                        continue
                except (ValueError, TypeError):
                    continue
                link = post.get('permalink_url') or ''
                parsed = urlparse(link)
                if parsed.scheme != 'https' or parsed.hostname not in {'facebook.com', 'www.facebook.com', 'm.facebook.com'}:
                    continue
                seen.add(post_id)
                message = (post.get('message') or '').strip()
                items.append({'id': post_id, 'page_id': pid, 'page_name': page.get('name') or '',
                              'text': message, 'title': message[:200], 'excerpt': message,
                              'published_at': date.astimezone(timezone.utc).isoformat(), 'url': link,
                              'image': post.get('full_picture') or ''})
            paging = data.get('paging') or {}
            after = (paging.get('cursors') or {}).get('after')
            if not paging.get('next') or not after:
                break
            if after in cursors:
                raise meta.SocialError('Meta repitió el cursor de lectura; no se pudo completar la consulta.')
            cursors.add(after)
            params['after'] = after  # Never follow a server-provided URL carrying tokens.
        return sorted(items, key=lambda x: x['published_at'], reverse=True)
    raise meta.SocialError('No hay acceso verificable a esta página. ' + REQUIREMENTS)


def configuration():
    """Report only configuration presence; never return credential or task values."""
    configured = os.getenv('FACEBOOK_READER_PROVIDER', '').strip().strip('\"\'').lower()
    token_present = bool(os.getenv('APIFY_TOKEN', '').strip())
    task_present = bool(os.getenv('APIFY_FACEBOOK_TASK_ID', '').strip())
    provider = configured or ('apify' if token_present and task_present else 'meta')
    return {'provider': provider if provider in {'meta', 'apify'} else 'invalid',
            'selection': 'explicit' if configured else 'automatic',
            'apify_token_present': token_present, 'apify_task_present': task_present,
            'reader_revision': '2026-11-06.1'}


def recent():
    items, pages = [], []
    config = configuration()
    provider = config['provider']
    requirements = REQUIREMENTS
    external_rows, extraction_at, external_error = None, None, None
    if provider == 'apify':
        from . import facebook_apify
        requirements = facebook_apify.REQUIREMENTS
    seen = set()
    for source in db.rows("SELECT * FROM sources WHERE brand='cofrade' AND kind='social' AND active=1 ORDER BY name"):
        if not account(source['url']):
            continue
        state = {'source_id': source['id'], 'name': source['name'], 'url': source['url']}
        if not source.get('official'):
            state.update(status='unverified', message='Comprueba que es la página oficial de la hermandad de La Línea y márcala como oficial en Gestionar → Editar.')
        else:
            try:
                if provider == 'apify':
                    if external_rows is None and external_error is None:
                        try:
                            external_rows, extraction_at = facebook_apify.fetch()
                        except meta.SocialError as exc:
                            external_error = str(exc)
                    if external_error:
                        raise meta.SocialError(external_error)
                    posts = facebook_apify.page_posts(source['url'], external_rows)
                elif provider == 'meta':
                    posts = page_posts(source['url'])
                else:
                    raise meta.SocialError('FACEBOOK_READER_PROVIDER debe ser meta o apify.')
                for post in posts:
                    if post['id'] not in seen:
                        items.append(dict(post, brotherhood=source['name'], source_id=source['id']))
                        seen.add(post['id'])
                state.update(status='ok', count=len(posts))
            except meta.SocialError as exc:
                message = str(exc) if provider != 'meta' else 'Meta no permite verificar o leer esta página. ' + REQUIREMENTS
                state.update(status='unavailable', message=message)
        pages.append(state)
    items.sort(key=lambda p: p['published_at'], reverse=True)
    return {'items': items, 'pages': pages, 'days': 7, 'requirements': requirements, 'provider': provider, 'extraction_at': extraction_at, 'configuration': config}
