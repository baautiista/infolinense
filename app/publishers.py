import requests
import html
from pathlib import Path
from .config import *

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
    if PUBLISH_MODE=='webhook':
        if not LOVABLE_WEBHOOK_URL: raise RuntimeError('LOVABLE_WEBHOOK_URL no configurada')
        h={'Content-Type':'application/json'}
        if LOVABLE_WEBHOOK_TOKEN:h['Authorization']='Bearer '+LOVABLE_WEBHOOK_TOKEN
        r=requests.post(LOVABLE_WEBHOOK_URL,json=payload,headers=h,timeout=30); r.raise_for_status()
        try:d=r.json()
        except:d={}
        return d.get('url') or d.get('public_url') or ''
    if PUBLISH_MODE=='supabase':
        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY: raise RuntimeError('Supabase no configurado')
        url=f"{SUPABASE_URL}/rest/v1/{SUPABASE_ARTICLES_TABLE}"
        h={'apikey':SUPABASE_SERVICE_ROLE_KEY,'Authorization':'Bearer '+SUPABASE_SERVICE_ROLE_KEY,'Content-Type':'application/json','Prefer':'return=representation'}
        r=requests.post(url,json=payload,headers=h,timeout=30); r.raise_for_status(); d=r.json()
        return (d[0].get('url') or d[0].get('slug') or '') if d else ''
    raise RuntimeError('Publicación no configurada: usa webhook o Supabase en .env')
