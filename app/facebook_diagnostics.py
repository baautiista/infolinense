"""Diagnóstico de solo lectura. Devuelve una lista explícita de datos no secretos."""
from . import social_publish as sp


def diagnose(photo_ids=()):
    page = sp._meta()
    token, pid = page.get('access_token'), str(page.get('id') or '')
    result = {'brand': sp.current_brand(), 'selected_page_id': pid, 'photos': [], 'unknown': []}
    if not token or not pid:
        return dict(result, error='Facebook no está conectado para este medio.')

    def read(path, fields=None, **params):
        if fields:
            params['fields'] = fields
        return sp._graph('GET', path, params=dict(params, access_token=token))

    def optional(label, path, fields=None, **params):
        try:
            return read(path, fields, **params)
        except sp.SocialError as error:
            # Los códigos son suficientes para distinguir permisos/objeto ausente.
            result['unknown'].append({'check': label, 'code': getattr(error, 'code', None),
                                      'subcode': getattr(error, 'subcode', None)})
            return {}

    identity = optional('token_identity', '/me', 'id,name,is_published')
    result['page'] = {key: identity[key] for key in ('id', 'name', 'is_published') if key in identity}
    result['token_page_matches'] = str(identity.get('id')) == pid if identity.get('id') else None
    if sp.META_APP_ID and sp.META_APP_SECRET:
        try:
            debug = sp._graph('GET', '/debug_token', params={
                'input_token': token, 'access_token': sp.META_APP_ID + '|' + sp.META_APP_SECRET}).get('data') or {}
            result['token'] = {k: debug[k] for k in ('app_id', 'application', 'type', 'is_valid', 'profile_id', 'scopes', 'expires_at', 'data_access_expires_at') if k in debug}
            result['configured_app_matches'] = str(debug.get('app_id')) == str(sp.META_APP_ID) if debug.get('app_id') else None
        except sp.SocialError as error:
            result['unknown'].append({'check': 'token_app', 'code': getattr(error, 'code', None)})
    else:
        result['unknown'].append({'check': 'token_app', 'reason': 'Faltan credenciales de aplicación en el servidor.'})
    settings = optional('page_settings', '/' + pid + '/settings', 'setting,value')
    # No devolver ajustes ajenos a la visibilidad ni datos de la cuenta.
    result['audience_settings'] = [dict(setting=item['setting'], value=item.get('value'))
        for item in settings.get('data') or []
        if item.get('setting') in ('AGE_RESTRICTIONS', 'COUNTRY_RESTRICTIONS', 'age_restrictions', 'country_restrictions')]
    result['audience_settings_note'] = 'Que no aparezcan ajustes no confirma ausencia de restricciones; revisar Configuración de la página si Meta no los expone.'
    result['audience_settings_complete'] = 'data' in settings and not settings.get('paging', {}).get('next')
    published = optional('published_posts', '/' + pid + '/published_posts', 'id', limit=100) if photo_ids else {}
    published_ids = {str(item.get('id')) for item in published.get('data') or []}
    for photo_id in photo_ids:
        photo = optional('photo:' + photo_id, '/' + photo_id, 'id,from,created_time,page_story_id')
        item = {'photo_id': photo_id, 'readable': bool(photo.get('id'))}
        item.update({k: photo[k] for k in ('id', 'created_time', 'page_story_id') if k in photo})
        owner = photo.get('from') or {}
        item['owner'] = {k: owner[k] for k in ('id', 'name') if k in owner}
        item['owner_matches'] = str(owner.get('id')) == pid if owner.get('id') else None
        story = photo.get('page_story_id')
        if story:
            # Consultar campo a campo: uno no soportado no oculta los demás.
            state = {}
            for field in ('from', 'is_published', 'is_hidden', 'scheduled_publish_time', 'privacy', 'targeting', 'feed_targeting', 'application', 'permalink_url'):
                data = optional('post:' + str(story) + ':' + field, '/' + str(story), field)
                if field in data:
                    value = data[field]
                    if field in ('from', 'application') and isinstance(value, dict):
                        value = {k: value[k] for k in ('id', 'name') if k in value}
                    state[field] = value
            item['post'] = state
            item['published_posts_found'] = str(story) in published_ids
            item['published_posts_search_complete'] = 'data' in published and not published.get('paging', {}).get('next')
        result['photos'].append(item)
    result['note'] = 'No cambia publicaciones. Un objeto ilegible no demuestra que sea privado o borrado. El modo de la aplicación se comprueba en Meta Developers.'
    return result
