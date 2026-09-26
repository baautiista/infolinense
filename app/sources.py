import re, html, difflib
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
from . import db
from .config import OPENAI_API_KEY, OPENAI_WEB_SEARCH
UA='InfoLinenseBot/1.0 (+editorial monitoring)'

def clean(s):
    s=BeautifulSoup(html.unescape(s or ''),'html.parser').get_text(' ',strip=True)
    return re.sub(r'\s+',' ',s).strip()

def fetch(url,timeout=18):
    return requests.get(url,timeout=timeout,headers={'User-Agent':UA,'Accept-Language':'es-ES,es;q=0.9'})

def parse_rss(source):
    r=fetch(source['url']); r.raise_for_status(); root=ET.fromstring(r.content); items=[]
    for it in root.findall('.//item')[:40]:
        title=clean(it.findtext('title')); link=clean(it.findtext('link')); desc=clean(it.findtext('description')); date=clean(it.findtext('pubDate'))
        if title and link: items.append({'title':title,'url':link,'excerpt':desc,'published_at':date})
    if not items:
        ns={'a':'http://www.w3.org/2005/Atom'}
        for it in root.findall('.//a:entry',ns)[:40]:
            title=clean(it.findtext('a:title',default='',namespaces=ns)); link=''; ln=it.find('a:link',ns)
            if ln is not None: link=ln.attrib.get('href','')
            desc=clean(it.findtext('a:summary',default='',namespaces=ns)); date=clean(it.findtext('a:updated',default='',namespaces=ns))
            if title and link: items.append({'title':title,'url':link,'excerpt':desc,'published_at':date})
    return items

def parse_html(source):
    r=fetch(source['url']); r.raise_for_status(); soup=BeautifulSoup(r.text,'html.parser'); items=[]; seen=set()
    for a in soup.select('a[href]'):
        title=clean(a.get_text(' ',strip=True))
        if len(title)<28: continue
        link=urljoin(source['url'],a.get('href'))
        if link in seen: continue
        seen.add(link)
        text=(title+' '+clean(a.parent.get_text(' ',strip=True) if a.parent else ''))[:900]
        if 'línea' in text.lower() or source.get('official'):
            items.append({'title':title[:260],'url':link,'excerpt':text[:600],'published_at':''})
        if len(items)>=50: break
    return items

def exact_locality(text):
    t=(text or '').lower()
    good=['la línea de la concepción','la línea','linense','atunara','poniente','levante','santa margarita','fariñas','saccone','el arenal']
    bad=['línea de metro','línea ferroviaria','línea eléctrica','línea aérea','línea editorial']
    if any(x in t for x in bad) and not any(x in t for x in ['la línea de la concepción','linense','atunara']): return False
    return any(x in t for x in good)

def heuristic_score(title,excerpt,source):
    text=(title+' '+(excerpt or '')).lower(); score=0
    if exact_locality(text): score+=28
    if source.get('official'): score+=14
    score+=round(int(source.get('priority') or 50)*0.10)
    for kw in ['obra','urbanismo','licit','contrato','presupuesto','adjudic','vivienda','frontera','gibraltar','empleo','apertura','comercio','agenda','cultura','patrimonio','servicio','tráfico','movilidad','subvención','ayuda','playa','parque','puerto','hospital']:
        if kw in text: score+=4
    nums=re.findall(r'\b\d[\d\.,]*\b',text)
    if nums: score+=min(10,len(nums)*2)
    return max(0,min(100,score))

def is_duplicate(title,url):
    if url and db.row('SELECT id FROM candidates WHERE url=?',(url,)): return True
    nt=re.sub(r'\W+',' ',title.lower())
    for x in db.rows('SELECT title FROM candidates ORDER BY id DESC LIMIT 120'):
        pt=re.sub(r'\W+',' ',x['title'].lower())
        if difflib.SequenceMatcher(None,nt,pt).ratio()>0.88: return True
    return False

def add_candidate(title,url,excerpt,source_name,source_id=None,published_at='',source_meta=None):
    if not title or not url or is_duplicate(title,url): return None
    source_meta=source_meta or {'priority':60,'official':0}
    if not exact_locality(title+' '+(excerpt or '')) and not source_meta.get('official'): return None
    score=heuristic_score(title,excerpt,source_meta); rel='low' if score<50 else ('medium' if score<70 else 'high')
    return db.exec_('''INSERT OR IGNORE INTO candidates(source_id,source_name,title,url,published_at,excerpt,score,relevance,status) VALUES(?,?,?,?,?,?,?,?,?)''',(source_id,source_name,title,url,published_at,excerpt,score,rel,'new'))

def scan_all():
    added=[]; errors=[]
    for s in db.rows('SELECT * FROM sources WHERE active=1 ORDER BY priority DESC'):
        try:
            its=parse_rss(s) if s['kind']=='rss' else parse_html(s)
            for it in its:
                cid=add_candidate(it['title'],it['url'],it.get('excerpt',''),s['name'],s['id'],it.get('published_at',''),s)
                if cid: added.append(cid)
        except Exception as e: errors.append(f"{s['name']}: {e}")
    if OPENAI_API_KEY and OPENAI_WEB_SEARCH:
        try:
            from . import ai
            for it in ai.discover_candidates():
                meta={'priority':85 if it.get('official') else 65,'official':1 if it.get('official') else 0}
                cid=add_candidate(clean(it.get('title','')),clean(it.get('url','')),clean(it.get('excerpt','')),clean(it.get('source_name','Web')),None,'',meta)
                if cid: added.append(cid)
        except Exception as e: errors.append('Descubrimiento web IA: '+str(e))
    db.log('scan',f'Escaneo: {len(added)} nuevas; {len(errors)} errores')
    return {'added':added,'errors':errors}

def fetch_article_text(url):
    try:
        r=fetch(url); r.raise_for_status(); soup=BeautifulSoup(r.text,'html.parser')
        for x in soup(['script','style','nav','footer','header','aside']): x.decompose()
        article=soup.find('article') or soup.find('main') or soup.body
        return clean(article.get_text(' ',strip=True) if article else soup.get_text(' ',strip=True))[:18000]
    except Exception: return ''
