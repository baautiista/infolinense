import re, hashlib
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from PIL import Image
from io import BytesIO
from .config import UPLOAD_DIR
from . import db
UA='Mozilla/5.0 InfoLinenseEditorial/2.0'

def source_images(url, official=False):
    out=[]
    if not url: return out
    try:
        r=requests.get(url,timeout=20,headers={'User-Agent':UA}); r.raise_for_status(); soup=BeautifulSoup(r.text,'html.parser')
        candidates=[]
        for key in [('property','og:image'),('name','twitter:image')]:
            tag=soup.find('meta',attrs={key[0]:key[1]})
            if tag and tag.get('content'): candidates.append(urljoin(url,tag['content']))
        for img in soup.select('article img[src], main img[src]')[:12]:
            candidates.append(urljoin(url,img.get('src')))
        for u in candidates:
            if u:
                out.append({'url':u,'source':url,'license':'Fuente oficial: comprobar condiciones de reutilización' if official else 'Fuente externa: requiere permiso o licencia antes de publicar','author':'','kind':'official_source' if official else 'external_source','publish_safe':False})
    except Exception: pass
    seen=set(); uniq=[]
    for x in out:
        if x['url'] not in seen: seen.add(x['url']); uniq.append(x)
    return uniq[:8]

def commons_images(query):
    api='https://commons.wikimedia.org/w/api.php'
    params={'action':'query','generator':'search','gsrsearch':query,'gsrnamespace':6,'gsrlimit':10,'prop':'imageinfo','iiprop':'url|extmetadata','iiurlwidth':1800,'format':'json'}
    out=[]
    try:
        d=requests.get(api,params=params,timeout=20,headers={'User-Agent':UA}).json()
        for p in (d.get('query',{}).get('pages',{}) or {}).values():
            ii=(p.get('imageinfo') or [{}])[0]; meta=ii.get('extmetadata') or {}
            u=ii.get('thumburl') or ii.get('url')
            if not u or not re.search(r'\.(jpe?g|png|webp)$',u.split('?')[0],re.I) and 'thumb' not in u: continue
            lic=(meta.get('LicenseShortName') or {}).get('value','').strip()
            author=re.sub('<[^>]+>','',(meta.get('Artist') or {}).get('value',''))
            out.append({'url':u,'source':'Wikimedia Commons','license':lic or 'Licencia no especificada','author':author,'kind':'commons','publish_safe':bool(lic)})
    except Exception: pass
    return out

def _source_is_official(candidate):
    sid=candidate.get('source_id')
    if not sid: return False
    s=db.row('SELECT official FROM sources WHERE id=?',(sid,))
    return bool(s and s.get('official'))

def search_real_photos(candidate,section):
    official=_source_is_official(candidate)
    source=source_images(candidate.get('url',''),official=official)
    safe=[x for x in source if x.get('publish_safe')]
    stop={'para','como','sobre','desde','hasta','entre','tras','ante','línea','linea','concepción','concepcion','ayuntamiento','municipal','linense','linenses'}
    words=[w for w in re.findall(r'[A-Za-zÁÉÍÓÚÑÜáéíóúñü]{5,}',candidate.get('title','')) if w.lower() not in stop][:3]
    commons=[]
    for q in ([' '.join(words)+' La Línea de la Concepción'] if words else [])+['La Línea de la Concepción '+section.title() if section else '','La Línea de la Concepción']:
        if not q.strip(): continue
        known={x['url'] for x in commons}
        commons+=[x for x in commons_images(q) if x['url'] not in known]
        if len(commons)>=8: break
    # Prefer photos with a documented reusable license; source images require a rights check.
    return (safe + [x for x in commons if x.get('publish_safe')] + [x for x in commons if not x.get('publish_safe')] + [x for x in source if not x.get('publish_safe')])[:16]

def download_image(url):
    r=requests.get(url,timeout=30,headers={'User-Agent':UA}); r.raise_for_status()
    im=Image.open(BytesIO(r.content)).convert('RGB')
    name=hashlib.sha1(url.encode()).hexdigest()[:16]+'.jpg'; path=UPLOAD_DIR/name
    im.save(path,'JPEG',quality=93)
    return str(path)
