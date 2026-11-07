"""Facebook: confirmación independiente del envío y reintentos de solo lectura.

No confunde un recurso fotográfico/vídeo con una publicación del muro.
La confirmación API no garantiza acceso anónimo (p. ej. app en desarrollo).
"""
import json
import os
from pathlib import Path

from . import brands, db


def _api():
    from . import social_publish
    return social_publish


def page_identity():
    sp = _api()
    page = sp._meta()
    pid, token = str(page.get('id') or ''), page.get('access_token')
    if not pid or not token:
        raise sp.SocialError('Facebook no está conectado para este medio.')
    expected = os.getenv(brands.BRANDS[sp.current_brand()]['env'] + 'META_PAGE_ID', '').strip()
    if expected and pid != expected:
        raise sp.SocialError('La página seleccionada no coincide con META_PAGE_ID del medio.')
    actual = sp._graph('GET', '/me', params={'fields': 'id,name,is_published', 'access_token': token})
    if str(actual.get('id')) != pid:
        raise sp.SocialError('El token pertenece a otra página. No se ha enviado contenido.')
    if actual.get('is_published') is not True:
        raise sp.SocialError('Meta no confirma que la página esté publicada.')
    if sp.current_brand() == 'cofrade':
        canonical = sp._graph('GET', '/elcofradelinense', params={'fields': 'id', 'access_token': token})
        if str(canonical.get('id')) != pid:
            raise sp.SocialError('La página conectada no es facebook.com/elcofradelinense.')
    return page


def verify(page, remote):
    """Solo GET; cualquier dato ausente o restricción deja la entrada sin confirmar."""
    sp = _api()
    pid, token = str(page['id']), page['access_token']
    wanted = str(remote)
    cursor = None
    found = None
    for _ in range(10):
        params = {'fields': 'id,attachments{target}', 'limit': 100, 'access_token': token}
        if cursor:
            params['after'] = cursor
        batch = sp._graph('GET', '/' + pid + '/published_posts', params=params)
        for post in batch.get('data') or []:
            targets = [str((a.get('target') or {}).get('id')) for a in (post.get('attachments') or {}).get('data', [])]
            if str(post.get('id')) == wanted or (wanted.startswith('fbv:') and wanted[4:] in targets):
                found = str(post['id'])
                break
        if found:
            break
        paging = batch.get('paging') or {}
        cursor = (paging.get('cursors') or {}).get('after') if paging.get('next') else None
        if not cursor:
            break
    if not found:
        raise sp.SocialError('El ID no figura entre las publicaciones publicadas de esta página (consulta limitada a 1000 entradas).')
    post = sp._graph('GET', '/' + found, params={
        'fields': 'id,from,permalink_url,is_published,is_hidden,scheduled_publish_time,privacy,targeting,feed_targeting',
        'access_token': token})
    if str((post.get('from') or {}).get('id')) != pid:
        raise sp.SocialError('El autor devuelto por Meta no coincide con la página del medio.')
    if post.get('is_published') is not True or post.get('is_hidden') is not False:
        raise sp.SocialError('Meta no confirma una entrada publicada y no oculta.')
    if post.get('scheduled_publish_time') or post.get('targeting') or post.get('feed_targeting'):
        raise sp.SocialError('La entrada tiene programación o restricciones de audiencia; requiere revisión.')
    if (post.get('privacy') or {}).get('value') != 'EVERYONE':
        raise sp.SocialError('Meta no confirma audiencia pública EVERYONE.')
    return found, post.get('permalink_url') or '', {
        'page_id': pid, 'post_id': found, 'is_published': True, 'is_hidden': False,
        'privacy': 'EVERYONE', 'published_posts': True,
        'anonymous_visibility': 'not_verified'}


def _init():
    db.exec_('''CREATE TABLE IF NOT EXISTS facebook_delivery(
        article_id INTEGER PRIMARY KEY, page_id TEXT NOT NULL,
        evidence TEXT, verified_at TEXT)''')


def publish(article):
    sp = _api()
    _init()
    aid = article['id']
    row = db.row("SELECT * FROM social_posts WHERE article_id=? AND network='facebook'", (aid,))
    delivery = db.row('SELECT * FROM facebook_delivery WHERE article_id=?', (aid,))
    remote = (row or {}).get('remote_id') or ''
    url = (row or {}).get('url') or ''
    started = False
    try:
        paths = None
        vid = None
        if not row:
            from . import video
            vid = video.get(aid)
            if not (vid and Path(vid['path']).is_file()):
                paths = sp.article_images(article)
        page = page_identity()
        if delivery and str(delivery['page_id']) != str(page['id']):
            raise sp.SocialError('La página conectada ha cambiado desde el envío; no se reenviará.')
        if not row:
            # Reserva atómica: dos peticiones simultáneas no pueden enviar dos entradas.
            with db.conn() as connection:
                claim = connection.execute("INSERT OR IGNORE INTO social_posts(article_id,network,status,message) VALUES(?,'facebook','publishing','Envío reservado; pendiente de confirmación')", (aid,))
                if claim.rowcount != 1:
                    return {'network': 'facebook', 'ok': False, 'message': 'Ya existe un envío; comprueba su estado.'}
                connection.execute('INSERT OR REPLACE INTO facebook_delivery(article_id,page_id) VALUES(?,?)', (aid, str(page['id'])))
            started = True
            if vid and Path(vid['path']).is_file():
                sp._publish_video('facebook', article, vid)
                result = db.row("SELECT * FROM social_posts WHERE article_id=? AND network='facebook'", (aid,))
                remote, url = result.get('remote_id') or '', result.get('url') or ''
            else:
                try:
                    texts = json.loads(article.get('network_texts') or '{}')
                    if (texts.get('facebook') or '').strip():
                        article = dict(article, body=texts['facebook'].strip())
                except (ValueError, TypeError):
                    pass
                remote, url = sp._facebook_post(page['id'], page['access_token'], sp.caption(article, 60000), paths[:10])
            sp._post_state(aid, 'facebook', 'verification_pending', remote, url, 'Enviada; pendiente de comprobar en Meta')
        elif not remote:
            raise sp.SocialError('Hay un intento previo sin ID confirmado. Requiere revisión antes de autorizar otro envío.')
        if not remote:
            raise sp.SocialError('El envío no devolvió un ID confirmado. No se repetirá automáticamente.')
        confirmed, url, evidence = verify(page, remote)
        sp._post_state(aid, 'facebook', 'published', confirmed, url, 'Publicación confirmada en Meta; acceso anónimo no comprobado')
        db.exec_('UPDATE facebook_delivery SET evidence=?,verified_at=CURRENT_TIMESTAMP WHERE article_id=?', (json.dumps(evidence), aid))
        return {'network': 'facebook', 'ok': True, 'confirmed': True, 'url': url, 'message': 'Publicación confirmada en Meta'}
    except Exception as exc:
        # Nunca conservar cuerpos HTTP, URLs con token ni excepciones de transporte.
        message = str(exc)[:400] if isinstance(exc, sp.SocialError) else 'No se pudo completar la comprobación de Facebook.'
        if row or started:
            sp._post_state(aid, 'facebook', 'verification_pending', remote, url, message)
        return {'network': 'facebook', 'ok': False, 'confirmed': False, 'url': url, 'message': message}


def audit():
    """Diagnóstico de todas las cuentas: solo lectura de Meta, sin crear publicaciones."""
    sp = _api()
    result = []
    for brand in brands.BRANDS:
        with sp.use_brand(brand):
            entry = {'brand': brand, 'posts': []}
            try:
                page = page_identity()
                entry.update(page_id=str(page['id']), page_name=page.get('name') or '')
                for row in db.rows("SELECT p.article_id,p.remote_id,p.status FROM social_posts p JOIN articles a ON a.id=p.article_id WHERE p.network='facebook' AND a.brand=? ORDER BY p.updated_at DESC LIMIT 20", (brand,)):
                    item = dict(row)
                    try:
                        _, _, item['evidence'] = verify(page, row.get('remote_id') or '')
                    except sp.SocialError as exc:
                        item['error'] = str(exc)
                    entry['posts'].append(item)
            except sp.SocialError as exc:
                entry['error'] = str(exc)
            result.append(entry)
    return result
