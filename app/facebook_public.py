"""Anonymous, best-effort public timeline reader. No paid service, cookies, or login.

The public timeline JSON approach is described by RSS-Bridge's FacebookBridge:
https://github.com/RSS-Bridge/rss-bridge/blob/master/bridges/FacebookBridge.php
This independently implemented reader deliberately rejects unverified authors and
reports partial coverage: publicly prefetched data does not cover the whole week.
"""
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from .social_publish import SocialError

HOSTS = {'facebook.com', 'www.facebook.com', 'm.facebook.com'}
HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
           'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
           'Accept-Language': 'es-ES,es;q=0.9', 'Upgrade-Insecure-Requests': '1'}
LIMITATION = 'Lectura pública gratuita parcial: Facebook puede mostrar solo la publicación más reciente. No se puede garantizar la cobertura de los últimos 7 días.'


def _walk(value):
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            yield current
            stack.extend(v for v in current.values() if isinstance(v, (list, dict)))
        elif isinstance(current, list):
            stack.extend(v for v in current if isinstance(v, (list, dict)))


def _owned_link(link, identities):
    from .facebook_reader import account
    if not isinstance(link, str):
        return False
    parsed = urlparse(link)
    if parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.username or parsed.password:
        return False
    parts = parsed.path.strip('/').split('/')
    if len(parts) >= 3 and parts[1] in {'posts', 'photos', 'videos'}:
        return parts[0].lower() in identities
    return parts[0] in {'permalink.php', 'story.php', 'photo.php'} and account(link) in identities


def html_payloads(html):
    soup = BeautifulSoup(html, 'html.parser')
    roots = []
    for script in soup.select('script[type="application/json"]'):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            roots.append(json.loads(raw))
        except (ValueError, TypeError):
            continue
    return roots


def parse(html, url, now=None):
    return parse_payloads(html_payloads(html), url, now)


def parse_payloads(roots, url, now=None):
    """Interpret actual browser payloads, including Page-backed profiles typed User by Facebook."""
    from .facebook_reader import account
    now = now or datetime.now(timezone.utc)
    wanted = account(url)
    if not wanted:
        raise SocialError('La fuente debe identificar una página oficial de Facebook.')
    nodes, delegated = [], set()
    for root in roots:
        for value in _walk(root):
            profile_id = str(value.get('id') or '')
            delegate = value.get('delegate_page')
            if profile_id.isdigit() and isinstance(delegate, dict) and str(delegate.get('id') or '').isdigit():
                delegated.add(profile_id)
            timeline = value.get('timeline_list_feed_units')
            if isinstance(timeline, dict) and isinstance(timeline.get('edges'), list):
                nodes.extend(edge['node'] for edge in timeline['edges'] if isinstance(edge, dict) and isinstance(edge.get('node'), dict))
            # Normal browser timeline responses also return Story nodes directly.
            if value.get('__typename') == 'Story' and value.get('post_id') and value.get('creation_time'):
                nodes.append(value)
    if not nodes:
        raise SocialError('Facebook no entregó un timeline público legible sin iniciar sesión. No se encontraron publicaciones verificables y no se ha recurrido a un servicio de pago.')
    items, seen, verified = [], set(), 0
    for node in nodes:
        sections = node.get('comet_sections') or {}
        content = sections.get('content') or {} if isinstance(sections, dict) else {}
        story = content.get('story') or node if isinstance(content, dict) else node
        if not isinstance(story, dict) or story.get('attached_story') or node.get('attached_story') or node.get('sharedPost'):
            continue
        actors = story.get('actors') or node.get('actors') or []
        if not isinstance(actors, list) or len(actors) != 1 or not isinstance(actors[0], dict):
            continue
        actor = actors[0]
        pid = str(actor.get('id') or '')
        actor_url = actor.get('url') or ''
        identities = {pid, account(actor_url) if isinstance(actor_url, str) else ''} - {''}
        if not (actor.get('__typename') == 'Page' or (actor.get('__typename') == 'User' and pid in delegated)) or not pid.isdigit() or wanted not in identities:
            continue
        post_id = str(node.get('post_id') or story.get('post_id') or '')
        stamp = node.get('creation_time', story.get('creation_time'))
        if not post_id or isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
            continue
        try:
            date = datetime.fromtimestamp(stamp, timezone.utc)
        except (ValueError, OverflowError, OSError):
            continue
        link = node.get('permalink_url') or story.get('permalink_url') or story.get('wwwURL') or story.get('url') or node.get('wwwURL') or node.get('url') or ''
        if not _owned_link(link, identities):
            continue
        verified += 1
        if not now-timedelta(days=7) <= date <= now:
            continue
        message = story.get('message') or node.get('message') or {}
        text = message.get('text') or '' if isinstance(message, dict) else ''
        if not isinstance(text, str):
            continue
        identity = pid+':'+post_id
        if identity in seen:
            continue
        seen.add(identity)
        image = ''
        attachments = story.get('attachments') or node.get('attachments') or []
        for value in _walk(attachments):
            photo = value.get('photo_image')
            uri = photo.get('uri') if isinstance(photo, dict) else None
            if isinstance(uri, str) and uri.startswith('https://'):
                image = uri
                break
        items.append({'id': identity, 'page_id': pid, 'page_name': actor.get('name') or '',
                      'text': text, 'title': text[:200], 'excerpt': text, 'published_at': date.isoformat(),
                      'url': link, 'image': image, 'provider': 'public'})
    if not verified:
        raise SocialError('El HTML público no permite verificar la página, el autor y la fecha. No se importan perfiles personales ni publicaciones de terceros.')
    return {'items': sorted(items, key=lambda item: item['published_at'], reverse=True), 'partial': True, 'message': LIMITATION}


def page_posts(url):
    from .facebook_reader import account
    if not account(url):
        raise SocialError('La fuente debe ser una página oficial de Facebook, no un grupo o enlace arbitrario.')
    try:
        response = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=False)
        if response.status_code in {301, 302, 303, 307, 308}:
            redirect = urlparse(response.headers.get('Location') or '')
            if (not redirect.hostname or redirect.hostname in HOSTS) and redirect.path.startswith(('/login', '/checkpoint')):
                raise SocialError('Facebook exige iniciar sesión antes de entregar el contenido de esta página a este lector. No se ha podido comprobar su timeline ni la cobertura de los últimos 7 días; no se ha utilizado un servicio de pago.')
        if response.status_code != 200:
            raise SocialError('No se pudo leer la página pública sin cuenta (HTTP %s). No se ha utilizado ningún servicio de pago.' % response.status_code)
        if len(response.content) > 12_000_000:
            raise SocialError('La página pública supera el tamaño permitido para esta lectura.')
        return parse(response.text, url)
    except requests.RequestException:
        raise SocialError('No se pudo conectar con Facebook para la lectura gratuita. Puede ser un bloqueo de red; no se ha utilizado un servicio de pago.') from None
