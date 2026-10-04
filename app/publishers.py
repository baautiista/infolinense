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


def web_payload(article):
    """Noticia lista para la web de InfoLinense (Lovable)."""
    family = layout.family_for(article.get('section'), article.get('headline'))
    photo = Path(article.get('image_local') or '')
    card = Path(article.get('render_path') or '')
    try: sources = json.loads(article.get('sources_json') or '[]')
    except Exception: sources = []
    paragraphs = [p.strip() for p in (article.get('body') or '').split('\n') if p.strip()]
    return {
        'external_id': 'desk-%s' % article['id'],
        'slug': slugify(article.get('headline')) + '-%s' % article['id'],
        'title': article.get('headline') or '',
        'subtitle': article.get('subtitle') or '',
        'body': article.get('body') or '',
        'body_html': ''.join('<p>' + html.escape(p) + '</p>' for p in paragraphs),
        'section': family[0], 'section_color': family[2], 'section_text_color': family[3],
        'source_url': article.get('source_url') or '',
        'source_name': article.get('outlet') or article.get('source_name') or '',
        'sources': [x for x in sources if isinstance(x, dict) and x.get('url')],
        'image_base64': base64.b64encode(photo.read_bytes()).decode() if photo.is_file() else None,
        'image_content_type': 'image/jpeg',
        'social_image_base64': base64.b64encode(card.read_bytes()).decode() if card.is_file() else None,
        'published_at': __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(timespec='seconds'),
        'status': 'published',
    }


def publish(article):
    payload={
      'section':article.get('section'),'title':article.get('headline'),'subtitle':article.get('subtitle'),
      'body':article.get('body'),'image':article.get('render_path') or article.get('image_url'),
      'status':'published'
    }
    if PUBLISH_MODE=='wordpress':
        if not (WORDPRESS_URL.startswith('https://') and WORDPRESS_USERNAME and WORDPRESS_APP_PASSWORD):
            raise RuntimeError('Configura WORDPRESS_URL, WORDPRESS_USERNAME y WORDPRESS_APP_PASSWORD en Railway')
        artwork=Path(article.get('render_path') or '')
        if not artwork.is_file():
            raise RuntimeError('No existe la imagen final exportada de Canva')
        endpoint=WORDPRESS_URL+'/wp-json/wp/v2'
        credentials=(WORDPRESS_USERNAME,WORDPRESS_APP_PASSWORD)
        identifier=str(article['id'])
        slug='infolinense-desk-'+identifier
        existing=requests.get(endpoint+'/posts',params={'slug':slug,'status':'any','context':'edit'},auth=credentials,timeout=20)
        existing.raise_for_status()
        posts=existing.json()
        if posts: return posts[0]['link']
        photo=requests.post(endpoint+'/media',data=artwork.read_bytes(),auth=credentials,
                            headers={'Content-Disposition':'attachment; filename="infolinense-'+identifier+'.png"',
                                     'Content-Type':'image/png'},timeout=45)
        photo.raise_for_status()
        media_id=photo.json()['id']
        paragraphs=''.join('<p>'+html.escape(p.strip())+'</p>' for p in (article.get('body') or '').split('\n') if p.strip())
        post=requests.post(endpoint+'/posts',auth=credentials,json={
            'title':article.get('headline') or 'InfoLinense',
            'excerpt':article.get('subtitle') or article.get('graphic_summary') or '',
            'content':paragraphs, 'slug':slug, 'featured_media':media_id, 'status':'publish'
        },timeout=45)
        post.raise_for_status()
        return post.json()['link']
    if PUBLISH_MODE in ('webhook','lovable'):
        if not LOVABLE_WEBHOOK_URL: raise RuntimeError('Falta LOVABLE_WEBHOOK_URL en Railway (la dirección de la función de la web)')
        secret=PUBLISH_WEBHOOK_SECRET or LOVABLE_WEBHOOK_TOKEN
        h={'Content-Type':'application/json'}
        if secret: h['Authorization']='Bearer '+secret; h['x-infolinense-secret']=secret
        r=requests.post(LOVABLE_WEBHOOK_URL,json=web_payload(article),headers=h,timeout=60)
        if not r.ok: raise RuntimeError('La web respondió HTTP %s: %s' % (r.status_code, r.text[:200]))
        try:d=r.json()
        except Exception:d={}
        return d.get('url') or d.get('public_url') or ''
    if PUBLISH_MODE=='supabase':
        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY: raise RuntimeError('Supabase no configurado')
        url=f"{SUPABASE_URL}/rest/v1/{SUPABASE_ARTICLES_TABLE}"
        h={'apikey':SUPABASE_SERVICE_ROLE_KEY,'Authorization':'Bearer '+SUPABASE_SERVICE_ROLE_KEY,'Content-Type':'application/json','Prefer':'return=representation'}
        r=requests.post(url,json=payload,headers=h,timeout=30); r.raise_for_status(); d=r.json()
        return (d[0].get('url') or d[0].get('slug') or '') if d else ''
    raise RuntimeError('Publicación no configurada: usa webhook o Supabase en .env')
