import requests
from .config import *

def publish(article):
    payload={
      'section':article.get('section'),'title':article.get('headline'),'subtitle':article.get('subtitle'),
      'body':article.get('body'),'image':article.get('render_path') or article.get('image_url'),
      'status':'published'
    }
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
