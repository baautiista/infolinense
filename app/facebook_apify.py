"""Read completed Apify task results. Never start or purchase scraping runs."""
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests

from . import social_publish as meta

API = 'https://api.apify.com/v2'
REQUIREMENTS = ('Configura FACEBOOK_READER_PROVIDER=apify, APIFY_TOKEN y APIFY_FACEBOOK_TASK_ID '
                'en el servidor. La tarea de Apify debe extraer exclusivamente las páginas oficiales '
                'y entregar identidad de página, autor y fecha comprobables. No requiere administrar '
                'las páginas en Meta. Hay que ejecutar/programar la tarea en Apify; Desk no la ejecuta.')


def _date(value):
    try:
        date = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return date.astimezone(timezone.utc) if date.tzinfo else None
    except (ValueError, TypeError):
        return None


def _get(path, token, **params):
    try:
        response = requests.get(API + path, headers={'Authorization': 'Bearer ' + token},
                                params=params, timeout=30, allow_redirects=False)
        if response.status_code in (401, 403):
            raise meta.SocialError('Apify rechazó el acceso. Revisa APIFY_TOKEN y los permisos de lectura de la tarea y su dataset.')
        if response.status_code == 404:
            if path.startswith('/actor-tasks/'):
                raise meta.SocialError('Apify no encuentra la tarea para este token (HTTP 404). Comprueba que APIFY_FACEBOOK_TASK_ID es el ID de la tarea y que APIFY_TOKEN pertenece a una cuenta con acceso a ella.')
            if path.startswith('/datasets/'):
                raise meta.SocialError('La tarea existe, pero Apify no encuentra su dataset de resultados (HTTP 404). Puede haber sido eliminado o no ser accesible con este token.')
        if response.status_code != 200:
            raise meta.SocialError('Apify no pudo entregar la extracción completada (HTTP %s).' % response.status_code)
        return response.json()
    except (requests.RequestException, ValueError):
        # Never expose request URLs, headers, tokens, or provider error bodies.
        raise meta.SocialError('No se pudo consultar Apify. Comprueba la red y el formato de su respuesta.') from None


def fetch(now=None, run_id=None):
    """Get a fixed dataset from the latest successful run, once per panel request."""
    now = now or datetime.now(timezone.utc)
    token = os.getenv('APIFY_TOKEN', '').strip()
    task = os.getenv('APIFY_FACEBOOK_TASK_ID', '').strip().replace('/', '~')
    if not token or not re.fullmatch(r'[A-Za-z0-9_-]+(?:~[A-Za-z0-9_-]+)?', task):
        raise meta.SocialError(REQUIREMENTS)
    if run_id:
        if not re.fullmatch(r'[A-Za-z0-9_-]+', run_id):
            raise meta.SocialError('El identificador de ejecución de Apify no es válido.')
        data = _get('/actor-runs/' + run_id, token)
        run = data.get('data') if isinstance(data, dict) else None
        if not isinstance(run, dict) or run.get('status') != 'SUCCEEDED':
            raise meta.SocialError('La extracción de Apify todavía no ha terminado correctamente.')
    else:
        # The list endpoint distinguishes an inaccessible task (404) from an existing
        # task with no successful runs (200, empty items); /runs/last conflates them.
        response = _get('/actor-tasks/' + task + '/runs', token, status='SUCCEEDED', desc=1, limit=1)
        data = response.get('data') if isinstance(response, dict) else None
        runs = data.get('items') if isinstance(data, dict) else None
        if not isinstance(runs, list):
            raise meta.SocialError('Apify devolvió una lista de ejecuciones con un formato no reconocido.')
        if not runs:
            raise meta.SocialError('La tarea existe, pero no tiene ejecuciones terminadas correctamente. El JSON exportado puede proceder del actor original, no de esta tarea guardada. Comprueba la pestaña Runs de esta tarea en Apify; guardar una tarea no copia las ejecuciones anteriores.')
        run = runs[0]
        if not isinstance(run, dict) or run.get('status') != 'SUCCEEDED':
            raise meta.SocialError('Apify no devolvió una ejecución completada correctamente de esta tarea.')
    if run.get('actorTaskId') and run['actorTaskId'] != task and '~' not in task:
        raise meta.SocialError('La ejecución no pertenece a la tarea configurada; no se importan sus resultados.')
    finished = _date(run.get('finishedAt'))
    dataset = str(run.get('defaultDatasetId') or '')
    if not finished or not now-timedelta(hours=24) <= finished <= now or not re.fullmatch(r'[A-Za-z0-9_-]+', dataset):
        raise meta.SocialError('La extracción de Apify falta o tiene más de 24 horas. Actualiza la tarea en Apify.')
    rows = []
    for offset in range(0, 10000, 500):
        batch = _get('/datasets/' + dataset + '/items', token, format='json', clean='true', offset=offset, limit=500)
        if not isinstance(batch, list) or any(not isinstance(row, dict) for row in batch):
            raise meta.SocialError('El dataset de Apify no tiene el formato de publicaciones esperado.')
        rows.extend(batch)
        if len(batch) < 500:
            return rows, finished.isoformat()
    raise meta.SocialError('La extracción supera el límite de 10.000 registros. Reduce la tarea a las hermandades y los últimos 7 días.')


def page_posts(url, rows, now=None):
    from .facebook_reader import account
    now = now or datetime.now(timezone.utc)
    wanted = account(url)
    if not wanted:
        raise meta.SocialError('La dirección de la página oficial no es válida.')
    items, seen, matched, rejected = [], set(), False, 0
    for row in rows:
        # Both the collected Page and the actual post author are required. The input URL alone proves nothing.
        page = row.get('page') or {}
        user = row.get('user') or {}
        if not isinstance(page, dict) or not isinstance(user, dict):
            continue
        page_url = page.get('url') or row.get('facebookUrl') or ''
        if not isinstance(page_url, str):
            continue
        page_id = str(page.get('id') or row.get('pageId') or row.get('facebookId') or '')
        if wanted not in {account(page_url), page_id}:
            continue
        matched = True
        library = row.get('pageAdLibrary') or {}
        has_library = isinstance(library, dict) and str(library.get('id') or '').isdigit()
        is_page = has_library or page.get('isPage') is True or page.get('type') == 'Page' or bool(page.get('category') or row.get('pageCategory'))
        author_id = str(user.get('id') or '')
        if not page_id.isdigit() or not is_page or author_id != page_id:
            rejected += 1
            continue
        # Shared material is excluded instead of attributing its nested text/photo to this Page.
        if row.get('sharedPost'):
            continue
        original = row.get('topLevelUrl')
        if row.get('facebookId'):
            from urllib.parse import urlparse
            original_parts = urlparse(original).path.strip('/').split('/') if isinstance(original, str) else []
            original_host = urlparse(original).hostname if isinstance(original, str) else ''
            if (original_host not in {'facebook.com', 'www.facebook.com', 'm.facebook.com'} or
                    len(original_parts) != 3 or original_parts[0] != page_id or original_parts[1] != 'posts' or
                    original_parts[2] != str(row.get('postId') or '')):
                rejected += 1
                continue
        author_link = user.get('profileUrl') or user.get('url') or ''
        if not isinstance(author_link, str):
            rejected += 1
            continue
        author_url = account(author_link)
        if author_url and author_url not in {page_id, account(page_url)}:
            rejected += 1
            continue
        date = _date(row.get('time') or row.get('publishedAt'))
        if not date or not now-timedelta(days=7) <= date <= now:
            continue
        link = row.get('url') or ''
        if not isinstance(link, str):
            rejected += 1
            continue
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(link)
        if parsed.path.strip('/').split('/')[0] == 'reel' and row.get('facebookId') and original:
            # The actor supplies a checked Page-owned post permalink for these reels.
            link = original
            parsed = urlparse(link)
        # A permalink must itself identify this Page, not just mention it in its text.
        owner = account(link)
        parts = parsed.path.strip('/').split('/')
        if parsed.scheme != 'https' or parsed.hostname not in {'facebook.com', 'www.facebook.com', 'm.facebook.com'}:
            rejected += 1
            continue
        if len(parts) >= 3 and parts[1] in {'posts', 'photos', 'videos'}:
            owner = parts[0].lower()
        elif parts[0] in {'permalink.php', 'story.php', 'photo.php'}:
            owner = parse_qs(parsed.query).get('id', [''])[0]
        else:
            rejected += 1
            continue
        if owner not in {page_id, account(page_url)}:
            rejected += 1
            continue
        post_id = str(row.get('postId') or row.get('id') or '')
        if not post_id or (post_id.startswith(page_id+'_') is False and '_' in post_id):
            rejected += 1
            continue
        identity = page_id + ':' + post_id
        if identity in seen:
            continue
        seen.add(identity)
        text = row.get('text') or ''
        if not isinstance(text, str):
            continue
        media = row.get('media') or []
        image = row.get('full_picture') or ''
        if not image and isinstance(media, list):
            image = next((m.get('thumbnail') for m in media if isinstance(m, dict) and isinstance(m.get('thumbnail'), str)), '')
        image_url = urlparse(image) if isinstance(image, str) else urlparse('')
        image = image if image_url.scheme == 'https' and image_url.hostname and not image_url.username else ''
        items.append({'id': identity, 'page_id': page_id, 'page_name': page.get('name') or user.get('name') or row.get('pageName') or '',
                      'text': text, 'title': text[:200], 'excerpt': text, 'published_at': date.isoformat(),
                      'url': link, 'image': image, 'provider': 'apify'})
    if not matched:
        for row in rows:
            failed_url = row.get('inputUrl') or row.get('url') or ''
            if row.get('error') and isinstance(failed_url, str) and account(failed_url) == wanted:
                if row['error'] == 'not_available':
                    raise meta.SocialError('Apify no pudo acceder al contenido público de esta página. Revisa la URL y su visibilidad en Facebook.')
                if row['error'] == 'no_items':
                    raise meta.SocialError('Apify no devolvió publicaciones de esta página. Puede no haber resultados o estar restringida; no se pudo verificar la lectura.')
        raise meta.SocialError('La extracción no acredita esta página. Comprueba que la tarea incluye su URL exacta y datos de identidad; no se interpreta como cero publicaciones.')
    if rejected and not items:
        raise meta.SocialError('Se descartaron los registros de esta fuente: falta identidad de página o autor, o pertenecen a terceros. Hay que revisar la salida de la tarea.')
    return sorted(items, key=lambda item: item['published_at'], reverse=True)


def paid_runs_allowed():
    return os.getenv('FACEBOOK_ALLOW_PAID', 'false').strip().lower() in {'true', '1', 'yes', 'on'}


def run_task(urls, progress):
    """Explicit user-triggered paid operation. Resume persisted runs instead of duplicating them."""
    import json
    if not paid_runs_allowed():
        raise meta.SocialError('Las extracciones de pago están desactivadas. No se ha iniciado ninguna tarea en Apify.')
    token = os.getenv('APIFY_TOKEN', '').strip()
    task = os.getenv('APIFY_FACEBOOK_TASK_ID', '').strip().replace('/', '~')
    if not token or not re.fullmatch(r'[A-Za-z0-9_-]+(?:~[A-Za-z0-9_-]+)?', task):
        raise meta.SocialError(REQUIREMENTS)
    if not urls:
        raise meta.SocialError('No hay páginas de hermandades activas y confirmadas como oficiales.')
    try:
        saved = json.loads(meta.db.get_setting('facebook_apify_run', '{}') or '{}')
    except ValueError:
        raise meta.SocialError('No se pudo recuperar la extracción anterior; revisa sus ejecuciones antes de iniciar otra.')
    run_id = saved.get('id') if saved.get('task') == task else None
    if not run_id:
        try:
            budget = float(os.getenv('APIFY_FACEBOOK_MAX_CHARGE_USD', '1'))
            if not 0 < budget <= 100:
                raise ValueError()
        except ValueError:
            raise meta.SocialError('APIFY_FACEBOOK_MAX_CHARGE_USD debe estar entre 0 y 100 dólares.')
        body = {'startUrls': [{'url': u} for u in dict.fromkeys(urls)], 'captionText': False,
                'onlyPostsNewerThan': (datetime.now(timezone.utc)-timedelta(days=7)).date().isoformat(),
                'resultsLimit': 100}
        # Persist a marker before POST: a network timeout may occur after Apify accepted the run.
        meta.db.set_setting('facebook_apify_run', json.dumps({'task': task, 'id': 'pending'}))
        progress('Iniciando extracción de Facebook')
        try:
            response = requests.post(API + '/actor-tasks/' + task + '/runs',
                                    headers={'Authorization': 'Bearer ' + token}, json=body,
                                    params={'maxTotalChargeUsd': budget}, timeout=30, allow_redirects=False)
            if response.status_code != 201:
                if 400 <= response.status_code < 500:
                    meta.db.set_setting('facebook_apify_run', '{}')
                raise meta.SocialError('Apify no pudo iniciar la tarea (HTTP %s). Revisa acceso, saldo y límites de la tarea.' % response.status_code)
            run = response.json().get('data') or {}
            run_id = run.get('id')
            if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', run_id):
                raise ValueError()
            meta.db.set_setting('facebook_apify_run', json.dumps({'task': task, 'id': run_id}))
        except (requests.RequestException, ValueError):
            raise meta.SocialError('No se pudo confirmar el inicio de Apify. Revisa Runs en Apify antes de repetir; la solicitud podría haberse aceptado.') from None
    if run_id == 'pending':
        raise meta.SocialError('El inicio anterior quedó sin confirmar. Revisa Runs en Apify para evitar dos extracciones cobradas; se necesita recuperar su ID.')
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        data = _get('/actor-runs/' + run_id, token)
        run = data.get('data') if isinstance(data, dict) else None
        if not isinstance(run, dict):
            raise meta.SocialError('Apify no devolvió el estado de la extracción.')
        status = run.get('status')
        if status == 'SUCCEEDED':
            return run_id
        if status in {'FAILED', 'ABORTED', 'TIMED-OUT'}:
            meta.db.set_setting('facebook_apify_run', '{}')
            raise meta.SocialError('La extracción de Apify terminó con estado ' + status + '. Revisa el registro y el límite de gasto en Apify.')
        if status not in {'READY', 'RUNNING', 'ABORTING', 'TIMING-OUT'}:
            raise meta.SocialError('Apify devolvió un estado de ejecución desconocido; la ejecución pendiente se conserva para revisarla.')
        progress('Facebook: extracción en curso en Apify')
        time.sleep(3)
    raise meta.SocialError('Apify sigue trabajando. Pulsa Buscar más tarde para continuar la consulta sin iniciar otra extracción.')
