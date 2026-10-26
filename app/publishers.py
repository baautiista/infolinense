import requests
import html
from pathlib import Path
from .config import *

import base64, json, re, unicodedata
from . import layout


def slugify(text):
    text = unicodedata.normalize('NFD', str(text or '').lower())
    text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^a-z0-9]+', '-', text).strip('-')[:90] or 'noticia'


def _b64_image(path, fmt, width, limit=2_500_000):
    """Imagen en base64 para la web; si es muy grande se reduce (las enormes hacen fallar el guardado en la web)."""
    if not path.is_file():
        return None
    raw = path.read_bytes()
    if len(raw) > limit:
        from io import BytesIO
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert('RGB')
            if im.width > width:
                im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
            buf = BytesIO()
            im.save(buf, fmt, **({'quality': 85, 'optimize': True} if fmt == 'JPEG' else {'optimize': True}))
            raw = buf.getvalue()
    return base64.b64encode(raw).decode()


def clean_cover(article):
    """Foto para la portada de la web: nunca la imagen terminada de Canva (con logo, sección, titular y
    entradilla). Si la noticia solo tiene la imagen terminada, la web sale sin foto."""
    if (article.get('image_kind') or '') == 'final_image':
        return None
    photo = Path(article.get('image_local') or '')
    return photo if photo.is_file() else None


def web_payload(article):
    """Noticia lista para la web de InfoLinense (Lovable)."""
    family = layout.family_for(article.get('section'), article.get('headline'))
    photo = clean_cover(article)
    card = Path(article.get('render_path') or '')
    try: sources = json.loads(article.get('sources_json') or '[]')
    except Exception: sources = []
    paragraphs = [p.strip() for p in (article.get('body') or '').split('\n') if p.strip()]
    return {
        'external_id': 'desk-%s' % article['id'],
        # la web recorta el slug a 80 caracteres: el número de la noticia tiene que caber o dos noticias con el mismo
        # titular chocan («No se pudo guardar la noticia»)
        'slug': slugify(article.get('headline'))[:68].strip('-') + '-%s' % article['id'],
        'title': article.get('headline') or '',
        'subtitle': article.get('subtitle') or '',
        'body': article.get('body') or '',
        'body_html': ''.join('<p>' + html.escape(p) + '</p>' for p in paragraphs),
        'section': family[0], 'section_color': family[2], 'section_text_color': family[3],
        'source_url': article.get('source_url') or '',
        'source_name': article.get('outlet') or article.get('source_name') or '',
        'sources': [x for x in sources if isinstance(x, dict) and x.get('url')],
        'image_base64': _b64_image(photo, 'JPEG', 1600) if photo else None,
        'image_content_type': 'image/jpeg',
        'social_image_base64': _b64_image(card, 'PNG', 1080),
        'published_at': __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(timespec='seconds'),
        'status': 'published',
    }


def publish(article):
    """Publica en la web del medio de la noticia (InfoLinense, El Cofrade Linense o Carnavalinense)."""
    from . import brands
    slug = brands.valid(article.get('brand'))
    name = brands.BRANDS[slug]['name']
    pre = brands.BRANDS[slug]['env']
    cfg = brands.web_config(slug)
    mode = cfg['mode']
    if mode == 'wordpress':
        if not (cfg['wp_url'].startswith('https://') and cfg['wp_user'] and cfg['wp_password']):
            raise RuntimeError('Configura %sWORDPRESS_URL, %sWORDPRESS_USERNAME y %sWORDPRESS_APP_PASSWORD en Railway' % (pre, pre, pre))
        cover = clean_cover(article)  # portada limpia: la foto, sin los gráficos de Canva
        endpoint = cfg['wp_url'] + '/wp-json/wp/v2'
        credentials = (cfg['wp_user'], cfg['wp_password'])
        identifier = str(article['id'])
        slug_wp = 'infolinense-desk-' + identifier
        existing = requests.get(endpoint + '/posts', params={'slug': slug_wp, 'status': 'any', 'context': 'edit'}, auth=credentials, timeout=20)
        existing.raise_for_status()
        posts = existing.json()
        if posts: return posts[0]['link']
        media_id = None
        if cover:
            photo = requests.post(endpoint + '/media', data=cover.read_bytes(), auth=credentials,
                                  headers={'Content-Disposition': 'attachment; filename="%s-%s.jpg"' % (slugify(name), identifier),
                                           'Content-Type': 'image/jpeg'}, timeout=45)
            photo.raise_for_status()
            media_id = photo.json()['id']
        paragraphs = ''.join('<p>' + html.escape(p.strip()) + '</p>' for p in (article.get('body') or '').split('\n') if p.strip())
        post = requests.post(endpoint + '/posts', auth=credentials, json={
            'title': article.get('headline') or name,
            'excerpt': article.get('subtitle') or article.get('graphic_summary') or '',
            'content': paragraphs, 'slug': slug_wp, 'status': 'publish',
            **({'featured_media': media_id} if media_id else {})
        }, timeout=45)
        post.raise_for_status()
        return post.json()['link']
    if mode in ('webhook', 'lovable'):
        if not cfg['webhook_url']:
            raise RuntimeError('Falta %s en Railway (la dirección de la función de la web de %s)'
                               % ('LOVABLE_WEBHOOK_URL' if not pre else pre + 'WEBHOOK_URL', name))
        h = {'Content-Type': 'application/json'}
        if cfg['secret']: h['Authorization'] = 'Bearer ' + cfg['secret']; h['x-infolinense-secret'] = cfg['secret']
        payload = web_payload(article)
        payload['brand'] = slug
        if slug != brands.DEFAULT:
            payload['section'] = (article.get('section') or '').upper()
        r = requests.post(cfg['webhook_url'], json=payload, headers=h, timeout=60)
        if not r.ok:
            size = len(json.dumps(payload)) / 1_000_000
            raise RuntimeError('La web de %s respondió HTTP %s: %s (envío de %.1f MB)' % (name, r.status_code, r.text[:200], size))
        try: d = r.json()
        except Exception: d = {}
        return d.get('url') or d.get('public_url') or ''
    if mode == 'supabase' and slug == brands.DEFAULT:
        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY: raise RuntimeError('Supabase no configurado')
        payload = {'section': article.get('section'), 'title': article.get('headline'), 'subtitle': article.get('subtitle'),
                   'body': article.get('body'), 'image': article.get('render_path') or article.get('image_url'), 'status': 'published'}
        url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_ARTICLES_TABLE}"
        h = {'apikey': SUPABASE_SERVICE_ROLE_KEY, 'Authorization': 'Bearer ' + SUPABASE_SERVICE_ROLE_KEY, 'Content-Type': 'application/json', 'Prefer': 'return=representation'}
        r = requests.post(url, json=payload, headers=h, timeout=30); r.raise_for_status(); d = r.json()
        return (d[0].get('url') or d[0].get('slug') or '') if d else ''
    raise RuntimeError('La web de %s no está configurada en Railway' % name)
