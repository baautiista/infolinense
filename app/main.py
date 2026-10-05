import os, json, threading, time, ipaddress, zipfile
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
from io import BytesIO
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Depends
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from . import db, sources, pipeline, publishers, photos, canva, ai, planner, layout, social
from .auth import login, require_auth
from .config import BASE_DIR, RENDER_DIR, UPLOAD_DIR, SCAN_INTERVAL_MINUTES, AUTO_PIPELINE, OPENAI_API_KEY, ADMIN_PASSWORD, JWT_SECRET, PUBLISH_MODE, CORS_ORIGINS, PUBLIC_BASE_URL, AUTO_PUBLISH

VERSION='4.3.0'
app=FastAPI(title='InfoLinense Desk',version=VERSION)
app.add_middleware(CORSMiddleware,allow_origins=CORS_ORIGINS,allow_credentials=False,allow_methods=['*'],allow_headers=['Authorization','Content-Type'])
db.init_db()

class LoginIn(BaseModel): password:str
class SourceIn(BaseModel):
    name:str
    url:str
    kind:str='rss'
    priority:int=50
    official:bool=False
    local_scope:bool=False
class EditArticle(BaseModel):
    section:str|None=None; headline:str|None=None; subtitle:str|None=None; body:str|None=None; graphic_summary:str|None=None; image_headline:str|None=None
class PhotoChoice(BaseModel): url:str; source:str=''; license:str=''; author:str=''; confirm_permission:bool=False
class TriageIn(BaseModel): priority:str; planned_at:str|None=None
class CarouselPhoto(BaseModel): url:str; source:str=''; license:str=''; author:str=''; publish_safe:bool=False
class CarouselDesignIn(BaseModel): photo_urls:list[str]



def ai_error_text(e):
    """Human message plus what the provider really answered."""
    def one(err):
        name=ai.PROVIDER_NAMES.get(err.provider,err.provider or 'IA')
        tech=' · '.join(x for x in [f'HTTP {err.status}' if err.status else '',err.code or '',err.raw or ''] if x)
        text=f'{name}: {err}'
        if tech: text+=f' (respuesta del proveedor: {tech})'
        if err.retry_after: text+=f' Reintenta en {err.retry_after} s.'
        return text
    text=one(e)
    for extra in getattr(e,'fallback_errors',[]) or []:
        text+=' — También se probó '+one(extra)
    return text

@app.exception_handler(ai.AIProviderError)
async def ai_error_handler(request,exc):
    status=503 if exc.kind in ('rate_limit','overloaded','network','empty','error') else 502
    headers={'Retry-After':str(exc.retry_after)} if exc.retry_after else None
    return JSONResponse(status_code=status,content={'detail':ai_error_text(exc),'error':exc.as_dict()},headers=headers)

@app.post('/api/auth/login')
def auth_login(body:LoginIn): return {'token':login(body.password)}
@app.get('/api/health')
def health():
    return {'ok':True,'version':VERSION,'auth_configured':bool(ADMIN_PASSWORD and JWT_SECRET),'openai_configured':bool(OPENAI_API_KEY),'claude_configured':bool(ai.ANTHROPIC_API_KEY),'ai_configured':ai.AI_ENABLED,'ai_provider':ai.ACTIVE_PROVIDER or None,'ai_fallback':[p for p in ai.provider_chain()[1:]],'draft_provider':ai.ACTIVE_PROVIDER or 'source_draft','draft_mode':'ai' if ai.AI_ENABLED else 'source_draft','web_search':{p:ai.web_enabled(p) for p in ai.provider_chain()},'publish_mode':PUBLISH_MODE,'auto_publish':bool(AUTO_PUBLISH and PUBLISH_MODE!='none'),'paid_ai_allowed':ai.AI_ALLOW_PAID}

@app.get('/api/ai/check',dependencies=[Depends(require_auth)])
def ai_check():
    """Real, tiny request to each configured AI provider: shows exactly what it answers."""
    return ai.check_providers()
@app.get('/api/capabilities',dependencies=[Depends(require_auth)])
def capabilities():
    return {'real_photo_only':True,'ai_image_generation':False,'max_social_chars':2200,'ai_configured':ai.AI_ENABLED,'ai_provider':ai.ACTIVE_PROVIDER or None,'draft_provider':ai.ACTIVE_PROVIDER or 'source_draft','auto_pipeline':AUTO_PIPELINE,'public_base_url':PUBLIC_BASE_URL,'canva_configured':canva.ready(),'canva_connected':canva.connected(),'canva_redirect_uri':canva.callback_url(),'primary_template':'canva' if canva.ready() and canva.connected() else 'unavailable','canva_template_url':'https://www.canva.com/brand/brand-templates/'+canva.TEMPLATE_ID if canva.TEMPLATE_ID else None}

@app.get('/api/sections',dependencies=[Depends(require_auth)])
def sections():
    return [{'name':f[0],'page':f[1],'background':f[2],'color':f[3]} for f in layout.FAMILIES]

@app.get('/api/canva/connect',dependencies=[Depends(require_auth)])
def connect_canva():
    try: return {'url':canva.authorization_url()}
    except ValueError as e: raise HTTPException(400,str(e))

@app.get('/api/canva/template',dependencies=[Depends(require_auth)])
def canva_template_status():
    try: return canva.template_status()
    except ValueError as e: raise HTTPException(400,str(e))

@app.get('/api/canva/permissions',dependencies=[Depends(require_auth)])
def canva_permissions():
    try: return canva.permission_status()
    except ValueError as e: raise HTTPException(400,str(e))

@app.get('/api/canva/callback')
def canva_callback(code:str='',state:str='',error:str=''):
    if error: raise HTTPException(400,'Canva no autorizó la conexión')
    try: canva.complete(code,state)
    except ValueError as e: raise HTTPException(400,str(e))
    return RedirectResponse((PUBLIC_BASE_URL or '')+'/#settings',status_code=303)

@app.post('/api/articles/{aid}/canva',dependencies=[Depends(require_auth)])
def create_canva_design(aid:int):
    a=db.row('SELECT * FROM articles WHERE id=?',(aid,))
    if not a: raise HTTPException(404)
    try: return canva.create_design(a)
    except ValueError as e: raise HTTPException(400,str(e))
@app.post('/api/articles/{aid}/canva/export',dependencies=[Depends(require_auth)])
def export_canva_design(aid:int):
    a=db.row('SELECT * FROM articles WHERE id=?',(aid,))
    if not a: raise HTTPException(404)
    try: return canva.export_design(a)
    except ValueError as e: raise HTTPException(400,str(e))
@app.get('/api/dashboard',dependencies=[Depends(require_auth)])
def dashboard():
    return {'counts':{'new':db.row("SELECT COUNT(*) n FROM candidates WHERE status='new'")['n'],
                      'researched':db.row("SELECT COUNT(*) n FROM candidates WHERE status='researched'")['n'],
                      'draft':db.row("SELECT COUNT(*) n FROM candidates WHERE status='draft'")['n'],
                      'review':db.row("SELECT COUNT(*) n FROM articles WHERE status='review_ready'")['n'],
                      'approved':db.row("SELECT COUNT(*) n FROM articles WHERE status='approved'")['n'],
                      'published':db.row("SELECT COUNT(*) n FROM articles WHERE status='published'")['n']},
            'latest':db.rows("SELECT * FROM candidates WHERE status IN ('new','researched','draft') ORDER BY id DESC LIMIT 15"),
            'activity':db.rows('SELECT * FROM activity ORDER BY id DESC LIMIT 12')}
@app.get('/api/radar/stats',dependencies=[Depends(require_auth)])
def radar_stats():
    midnight=datetime.now(ZoneInfo('Europe/Madrid')).replace(hour=0,minute=0,second=0,microsecond=0)
    start=midnight.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    entries=db.rows('SELECT published_at FROM candidates WHERE created_at>=?',(start,))
    dated=[sources.publication_datetime(x['published_at']) for x in entries]
    confirmed=sum(1 for date in dated if date and date>=midnight.astimezone(timezone.utc))
    undated=sum(1 for date in dated if date is None)
    return {'date':midnight.date().isoformat(),'timezone':'Europe/Madrid','target':50,'found':confirmed,
            'discovered_today':len(entries),'undated_today':undated,
            'source_count':db.row('SELECT COUNT(*) n FROM sources WHERE active=1')['n'],
            'sources':db.rows('''SELECT s.id,s.name,s.kind,s.active,s.last_checked_at,s.last_success_at,
                       s.last_error,s.items_seen,s.items_added,
                       (SELECT COUNT(*) FROM candidates c WHERE c.source_id=s.id AND c.created_at>=?) today
                       FROM sources s ORDER BY s.priority DESC''',(start,))}
@app.post('/api/scan',dependencies=[Depends(require_auth)])
def scan():
    r=sources.scan_all()
    if not r.get('busy'):
        if AUTO_PIPELINE: r['pipeline']=pipeline.auto_process(r['added'])
        try: planner.rebuild_schedule()
        except Exception as e: db.log('schedule_error',str(e)[:250])
        r['auto_drafts_queued']=pipeline.queue_useful_pending()
    return r
@app.get('/api/social',dependencies=[Depends(require_auth)])
def social_items():
    """Panel de redes: quejas y noticias públicas de páginas y grupos de Facebook de La Línea."""
    return {'items':social.items(),'watched':social.watched()}

@app.post('/api/social/scan',dependencies=[Depends(require_auth)])
def social_scan():
    return social.scan()

@app.get('/api/schedule',dependencies=[Depends(require_auth)])
def schedule():
    return planner.get_schedule()

@app.post('/api/schedule/rebuild',dependencies=[Depends(require_auth)])
def schedule_rebuild():
    return planner.rebuild_schedule()

@app.post('/api/candidates/{cid}/triage',dependencies=[Depends(require_auth)])
def triage_candidate(cid:int,body:TriageIn):
    priority=body.priority.strip().lower()
    allowed={'urgent','today','this_week','future','no_interest'}
    if priority not in allowed: raise HTTPException(400,'Elige Urgente, Hoy, Esta semana, Futuro o No me interesa')
    if not db.row('SELECT id FROM candidates WHERE id=?',(cid,)): raise HTTPException(404,'Noticia no encontrada')
    planned=None; locked=0
    if body.planned_at:
        try:
            planned_dt=datetime.fromisoformat(body.planned_at.replace('Z','+00:00'))
            if planned_dt.tzinfo is None: planned_dt=planned_dt.replace(tzinfo=ZoneInfo('Europe/Madrid'))
            planned=planned_dt.astimezone(ZoneInfo('Europe/Madrid')).isoformat(timespec='minutes')
            locked=1
        except ValueError: raise HTTPException(400,'La hora debe tener formato ISO 8601')
    if priority=='no_interest':
        db.exec_("UPDATE candidates SET editorial_priority=?,planned_at=NULL,plan_locked=0,plan_reason=NULL,status='archived' WHERE id=?",(priority,cid))
        db.exec_("UPDATE articles SET status='rejected' WHERE candidate_id=? AND status='draft'",(cid,))
    else:
        db.exec_("UPDATE candidates SET editorial_priority=?,planned_at=?,plan_locked=?,plan_reason=NULL,status=CASE WHEN status='archived' THEN 'new' ELSE status END WHERE id=?",(priority,planned,locked,cid))
    planner.rebuild_schedule()
    if priority in pipeline.USEFUL:
        pipeline.queue_auto_write(cid,priority)  # redacción automática de lo marcado como útil
    return db.row('SELECT * FROM candidates WHERE id=?',(cid,))

@app.get('/api/candidates',dependencies=[Depends(require_auth)])
def candidates(status:str='new'):
    base="""SELECT c.*,s.kind AS source_kind,s.official AS source_official,
                    s.local_scope AS source_local_scope,a.id AS article_id,a.status AS article_status
             FROM candidates c LEFT JOIN sources s ON s.id=c.source_id
             LEFT JOIN articles a ON a.candidate_id=c.id AND a.status!='rejected'"""
    if status=='pending':
        rows=db.rows(base+" WHERE c.status IN ('new','researched','draft','needs_config') OR (c.status='review_ready' AND c.editorial_priority!='no_interest')")
    else:
        rows=db.rows(base+' WHERE c.status=?',(status,))
    now=datetime.now(timezone.utc); out=[]
    for r in rows:
        r['group']=sources.source_group(r)
        if r['group']=='Redes sociales' and status=='pending': continue  # están en la pestaña Redes
        d=sources.publication_datetime(r.get('published_at'))  # solo la fecha real de publicación, nunca la de descubrimiento
        r['date_iso']=d.isoformat() if d else None
        if not d: d=sources.publication_datetime((r.get('created_at') or '').replace(' ','T')+'+00:00')
        keep=r.get('article_id') or (r.get('editorial_priority') or 'undecided') not in ('undecided','')
        days=sources.WINDOW_DAYS.get(r['group'],sources.MAX_CANDIDATE_AGE_DAYS)
        if not keep and d and d<now-timedelta(days=days): continue  # antigua: no se muestra
        out.append(r)
    order={g:i for i,g in enumerate(sources.BLOCKS)}
    out.sort(key=lambda r:r.get('date_iso') or '',reverse=True)  # lo más reciente primero
    out.sort(key=lambda r:order.get(r['group'],9))
    return out

# ---------- Trabajo por fases: 1 Ordenar · 2 Redacción · 3 Revisar · 4 Publicar ----------
def _sort_queue():
    rows=[r for r in candidates('pending') if not r.get('article_id') and (r.get('editorial_priority') or 'undecided') in ('undecided','')]
    rows+= [r for r in social.items() if not r.get('article_id') and (r.get('editorial_priority') or 'undecided') in ('undecided','')]
    return rows

def _drafting():
    rows=db.rows("""SELECT c.id,c.title,c.source_name,c.outlet,c.editorial_priority,c.planned_at,c.work_state,c.work_step,c.work_error,
                      a.id article_id,a.status article_status,a.headline
                      FROM candidates c LEFT JOIN articles a ON a.candidate_id=c.id AND a.status!='rejected'
                      WHERE c.status NOT IN ('archived','published') AND c.editorial_priority IN ('urgent','today','this_week','future')
                        AND (a.id IS NULL OR a.status='draft')""")
    order={'urgent':0,'today':1,'this_week':2,'future':3}
    rows.sort(key=lambda r:(order.get(r['editorial_priority'],9),r.get('planned_at') or '9999'))
    for r in rows:
        r['state']='ready' if r.get('article_id') else ('error' if r.get('work_state')=='error' else 'working' if pipeline.is_working(r['id']) or r.get('work_state')=='working' else 'queued')
    return rows

@app.get('/api/workflow',dependencies=[Depends(require_auth)])
def workflow():
    """Cuántas piezas hay en cada fase del día."""
    drafting=_drafting()
    publish=db.rows("SELECT id FROM articles WHERE status IN ('review_ready','approved')")
    published=db.row("SELECT COUNT(*) n FROM articles WHERE status='published' AND updated_at>=date('now')")['n']
    return {'sort':len(_sort_queue()),'drafting':sum(1 for r in drafting if r['state']!='ready'),
            'errors':sum(1 for r in drafting if r['state']=='error'),'review':sum(1 for r in drafting if r['state']=='ready'),
            'publish':len(publish),'published_today':published}

@app.get('/api/sort-queue',dependencies=[Depends(require_auth)])
def sort_queue():
    return _sort_queue()

@app.get('/api/drafting',dependencies=[Depends(require_auth)])
def drafting():
    return _drafting()

@app.post('/api/articles/{aid}/ready',dependencies=[Depends(require_auth)])
def mark_ready(aid:int):
    """Revisada: pasa a la fase Publicar."""
    a=db.row('SELECT id,candidate_id,status FROM articles WHERE id=?',(aid,))
    if not a: raise HTTPException(404)
    if a['status']=='draft':
        db.exec_("UPDATE articles SET status='review_ready',updated_at=CURRENT_TIMESTAMP WHERE id=?",(aid,))
        db.exec_("UPDATE candidates SET status='review_ready' WHERE id=?",(a['candidate_id'],))
    return {'ok':True}

@app.get('/api/to-publish',dependencies=[Depends(require_auth)])
def to_publish():
    rows=db.rows("""SELECT a.id,a.headline,a.section,a.status,a.image_local,a.publish_url,c.planned_at,c.editorial_priority,c.id candidate_id,
                      d.exported canva_exported FROM articles a LEFT JOIN candidates c ON c.id=a.candidate_id
                      LEFT JOIN canva_designs d ON d.article_id=a.id
                      WHERE a.status IN ('review_ready','approved') OR (a.status='published' AND a.updated_at>=date('now'))""")
    for r in rows: r['has_photo']=bool(r.pop('image_local',None))
    rows.sort(key=lambda r:(r['status']=='published',r.get('planned_at') or '9999'))
    return rows

@app.get('/api/articles',dependencies=[Depends(require_auth)])
def list_articles():
    """Todas las noticias redactadas que siguen vivas (borrador, revisión, aprobadas y publicadas recientes)."""
    rows=db.rows("""SELECT a.id,a.candidate_id,a.section,a.headline,a.status,a.image_url,a.updated_at,a.publish_url,
                    c.planned_at,c.editorial_priority,c.source_name,c.outlet,d.exported canva_exported
                    FROM articles a LEFT JOIN candidates c ON c.id=a.candidate_id LEFT JOIN canva_designs d ON d.article_id=a.id
                    WHERE a.status IN ('draft','review_ready','approved') OR (a.status='published' AND a.updated_at>=datetime('now','-3 day'))""")
    order={'urgent':0,'today':1,'this_week':2,'future':3}
    rows.sort(key=lambda r:(r['status']=='published',order.get(r.get('editorial_priority'),9),r.get('planned_at') or '9999'))
    return rows
@app.post('/api/candidates/{cid}/investigate',dependencies=[Depends(require_auth)])
def investigate(cid:int):
    try: return {'ok':True,'research':pipeline.investigate_candidate(cid)}
    except ValueError as e: raise HTTPException(400,str(e))
    except RuntimeError as e: raise HTTPException(404,str(e))
@app.post('/api/candidates/{cid}/draft',dependencies=[Depends(require_auth)])
def draft_candidate(cid:int):
    try:
        aid=pipeline.draft_candidate(cid)
        return db.row('SELECT * FROM articles WHERE id=?',(aid,))
    except ValueError as e: raise HTTPException(400,str(e))
    except RuntimeError as e: raise HTTPException(404,str(e))

@app.get('/api/candidates/{cid}/draft',dependencies=[Depends(require_auth)])
def get_candidate_draft(cid:int):
    a=pipeline.existing_article(cid)
    if not a: raise HTTPException(404,'Borrador no disponible')
    return a

def _work_status(cid):
    c=db.row('SELECT id,status,work_state,work_step,work_error FROM candidates WHERE id=?',(cid,))
    if not c: raise HTTPException(404,'Noticia no encontrada')
    a=pipeline.existing_article(cid)
    if pipeline.is_working(cid):
        state='working'
    elif not a and c.get('work_state')=='queued':
        state='working'
    elif a:
        state='done'
    else:
        state=c.get('work_state') if c.get('work_state')=='error' else 'idle'
    error=None
    if state=='error' and c.get('work_error'):
        try: error=json.loads(c['work_error'])
        except Exception: error={'message':c['work_error']}
        if error.get('provider') is not None and error.get('message'):
            err=ai.AIProviderError(error['message'],error.get('provider',''),error.get('http_status'),error.get('code',''),error.get('kind','error'),error.get('retry_after_seconds'),error.get('provider_message',''))
            error['detail']=ai_error_text(err)
        else:
            error['detail']=error.get('message')
    step_text={'queued':'En cola: se redactará automáticamente…','investigating':'Investigando la noticia y sus fuentes…','drafting':'Redactando el borrador…'}
    return {'candidate_id':cid,'state':state,'step':c.get('work_step') if state=='working' else None,
            'step_text':step_text.get(c.get('work_step') or '','Trabajando…') if state=='working' else None,
            'next_action':'open' if a else ('draft' if c['status']=='researched' else 'investigate_and_draft'),
            'article_id':a['id'] if a else None,'article':a if state=='done' else None,'error':error}

@app.get('/api/candidates/{cid}/write',dependencies=[Depends(require_auth)])
def write_status(cid:int):
    """Estado del botón Redactar: idle | working | done | error."""
    return _work_status(cid)

@app.post('/api/candidates/{cid}/write',dependencies=[Depends(require_auth)])
def write_candidate(cid:int,wait:int=100):
    """Botón «Redactar»: abre el borrador si existe; si no, investiga (si hace falta) y redacta.

    Espera hasta `wait` segundos. Si aún trabaja devuelve 202 con state=working:
    consulta GET /api/candidates/{id}/write cada pocos segundos.
    """
    if not db.row('SELECT id FROM candidates WHERE id=?',(cid,)): raise HTTPException(404,'Noticia no encontrada')
    a=pipeline.existing_article(cid)
    if a: return {**_work_status(cid),'action':'opened','article':a}
    if pipeline.is_working(cid): return JSONResponse(status_code=202,content=_work_status(cid))
    holder={}
    def run():
        try: holder['result']=pipeline.write_candidate(cid)
        except Exception as e: holder['error']=e
    t=threading.Thread(target=run,daemon=True); t.start()
    t.join(max(0,min(int(wait),240)))
    if t.is_alive(): return JSONResponse(status_code=202,content=_work_status(cid))
    err=holder.get('error')
    if isinstance(err,ai.AIProviderError): raise err
    if isinstance(err,ValueError) and str(err)=='busy': return JSONResponse(status_code=202,content=_work_status(cid))
    if isinstance(err,ValueError): raise HTTPException(400,str(err))
    if isinstance(err,RuntimeError): raise HTTPException(404,str(err))
    if err: raise HTTPException(500,'No se pudo redactar: '+str(err)[:200])
    result=holder['result']
    return {**_work_status(cid),'action':result['action'],'article':db.row('SELECT * FROM articles WHERE id=?',(result['article_id'],))}
@app.post('/api/candidates/{cid}/submit',dependencies=[Depends(require_auth)])
def submit_candidate(cid:int):
    try: return {'id':pipeline.submit_candidate(cid)}
    except ValueError as e: raise HTTPException(400,str(e))
@app.post('/api/candidates/{cid}/archive',dependencies=[Depends(require_auth)])
def archive_candidate(cid:int):
    c=db.row('SELECT id,status FROM candidates WHERE id=?',(cid,))
    if not c: raise HTTPException(404)
    if c['status'] not in ('new','researched','draft'): raise HTTPException(400,'Esta pieza ya está en revisión')
    db.exec_("UPDATE candidates SET status='archived' WHERE id=?",(cid,))
    db.exec_("UPDATE articles SET status='rejected' WHERE candidate_id=? AND status='draft'",(cid,))
    return {'ok':True}
@app.post('/api/candidates/{cid}/prepare',dependencies=[Depends(require_auth)])
def prepare(cid:int,research:bool=False):
    try:
        aid=pipeline.process_candidate(cid,force_research=research); return db.row('SELECT * FROM articles WHERE id=?',(aid,))
    except Exception as e: raise HTTPException(500,str(e))
@app.get('/api/review',dependencies=[Depends(require_auth)])
def review():
    return db.rows("""SELECT a.*,c.title source_title,c.url source_url,c.score,c.source_name,d.url canva_url,d.exported canva_exported FROM articles a LEFT JOIN candidates c ON c.id=a.candidate_id LEFT JOIN canva_designs d ON d.article_id=a.id WHERE a.status IN ('review_ready','approved') ORDER BY a.id DESC""")
@app.get('/api/articles/{aid}',dependencies=[Depends(require_auth)])
def article(aid:int):
    a=db.row('SELECT a.*,c.url source_url,c.score,c.source_name,c.outlet,c.planned_at FROM articles a LEFT JOIN candidates c ON c.id=a.candidate_id WHERE a.id=?',(aid,))
    if not a: raise HTTPException(404)
    return a
@app.get('/api/articles/{aid}/carousel',dependencies=[Depends(require_auth)])
def get_carousel(aid:int):
    a=db.row('SELECT * FROM articles WHERE id=?',(aid,))
    if not a: raise HTTPException(404,'Noticia no encontrada')
    try: data=json.loads(a.get('carousel_json') or '{}')
    except Exception: data={}
    designs=db.rows('SELECT slide_index,design_id,url,exported,image_url,image_source,image_license FROM carousel_designs WHERE article_id=? ORDER BY slide_index',(aid,))
    data.update({'article_id':aid,'carousel_suitable':bool(a.get('carousel_suitable')),'carousel_reason':a.get('carousel_reason') or '',
                 'designs':designs,'download_url':'/api/articles/%s/carousel/download' % aid if designs else None})
    return data

@app.post('/api/articles/{aid}/headlines',dependencies=[Depends(require_auth)])
def alternate_headlines(aid:int):
    a=db.row('SELECT * FROM articles WHERE id=?',(aid,))
    if not a: raise HTTPException(404,'Noticia no encontrada')
    headlines=ai.alternate_headlines(a)
    db.exec_('UPDATE articles SET headline_options_json=? WHERE id=?',(json.dumps(headlines,ensure_ascii=False),aid))
    return {'headlines':headlines}

@app.get('/api/articles/{aid}/headlines',dependencies=[Depends(require_auth)])
def get_headlines(aid:int):
    a=db.row('SELECT headline,headline_options_json FROM articles WHERE id=?',(aid,))
    if not a: raise HTTPException(404,'Noticia no encontrada')
    try: options=json.loads(a.get('headline_options_json') or '[]')
    except Exception: options=[]
    return {'current':a.get('headline'),'headlines':options}

@app.post('/api/articles/{aid}/carousel',dependencies=[Depends(require_auth)])
def generate_carousel(aid:int):
    a=db.row('SELECT * FROM articles WHERE id=?',(aid,))
    if not a: raise HTTPException(404,'Noticia no encontrada')
    result=ai.generate_carousel(a)
    db.exec_('UPDATE articles SET carousel_suitable=?,carousel_reason=?,carousel_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
             (int(result['suitable']),result['reason'],json.dumps(result,ensure_ascii=False),aid))
    return result

@app.post('/api/articles/{aid}/carousel/design',dependencies=[Depends(require_auth)])
def design_carousel(aid:int,body:CarouselDesignIn):
    a=db.row('SELECT * FROM articles WHERE id=?',(aid,))
    if not a: raise HTTPException(404,'Noticia no encontrada')
    try: data=json.loads(a.get('carousel_json') or '{}')
    except Exception: data={}
    slides=data.get('slides') or []
    if not data.get('suitable') or not slides: raise HTTPException(400,'Primero analiza y crea el texto del carrusel')
    try: candidates=json.loads(a.get('image_candidates_json') or '[]')
    except Exception: candidates=[]
    by_url={item.get('url'):item for item in candidates if isinstance(item,dict)}
    if not 1<=len(body.photo_urls)<=6: raise HTTPException(400,'Selecciona entre una y seis fotos')
    selected=[]
    for url in body.photo_urls:
        photo=by_url.get(url) or {'url':url,'source':'','kind':'web','publish_safe':True}
        if str(url).startswith('upload:'):
            raise HTTPException(400,'Para el carrusel elige fotos de la búsqueda')
        selected.append(photo)
    try: return canva.create_carousel_designs(a,slides,selected)
    except ValueError as e: raise HTTPException(400,str(e))
    except Exception as e: raise HTTPException(502,'Canva no pudo terminar el carrusel: '+str(e)[:180]) from e

@app.get('/api/articles/{aid}/carousel/slide/{slide_index}',dependencies=[Depends(require_auth)])
def carousel_slide(aid:int,slide_index:int):
    if not db.row('SELECT article_id FROM carousel_designs WHERE article_id=? AND slide_index=? AND exported=1',(aid,slide_index)):
        raise HTTPException(404,'Diapositiva no disponible')
    path=RENDER_DIR/('article_%s_carousel_%s.png' % (aid,slide_index))
    if not path.is_file(): raise HTTPException(404,'Diapositiva no disponible')
    return FileResponse(path,media_type='image/png',filename='infolinense-%s-carrusel-%s.png' % (aid,slide_index))

@app.get('/api/articles/{aid}/carousel/download',dependencies=[Depends(require_auth)])
def download_carousel(aid:int):
    a=db.row('SELECT carousel_json FROM articles WHERE id=?',(aid,))
    if not a: raise HTTPException(404,'Noticia no encontrada')
    designs=db.rows('SELECT slide_index,exported FROM carousel_designs WHERE article_id=? ORDER BY slide_index',(aid,))
    if len(designs)<3 or any(not row.get('exported') for row in designs):
        raise HTTPException(400,'Primero exporta todas las diapositivas en Canva')
    archive=RENDER_DIR/('infolinense-carrusel-%s.zip' % aid)
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as zf:
        for row in designs:
            path=RENDER_DIR/('article_%s_carousel_%s.png' % (aid,row['slide_index']))
            if not path.is_file(): raise HTTPException(404,'Falta una diapositiva exportada')
            zf.write(path,'infolinense-carrusel-%02d.png' % row['slide_index'])
    return FileResponse(archive,media_type='application/zip',filename='infolinense-carrusel-%s.zip' % aid)

@app.get('/api/articles/{aid}/kit',dependencies=[Depends(require_auth)])
def kit(aid:int):
    a=article(aid)
    design=db.row('SELECT url,exported FROM canva_designs WHERE article_id=?',(aid,)) or {}
    exported=bool(design.get('exported')) and (RENDER_DIR/f'article_{aid}.png').is_file()
    body=(a.get('body') or '')[:2200]
    copy_text='\n\n'.join(x for x in [a.get('headline') or '',a.get('subtitle') or '',body] if x.strip())
    try: options=json.loads(a.get('headline_options_json') or '[]')
    except Exception: options=[]
    try: missing=json.loads(a.get('missing_data_json') or '[]')
    except Exception: missing=[]
    try: srcs=json.loads(a.get('sources_json') or '[]')
    except Exception: srcs=[]
    photo_ok=bool(a.get('image_local')) and Path(a['image_local']).is_file()
    carousel=db.rows('SELECT slide_index,exported FROM carousel_designs WHERE article_id=? ORDER BY slide_index',(aid,))
    return {'id':aid,'section':a.get('section'),'headline':a.get('headline'),'subtitle':a.get('subtitle'),'text':body,'chars':len(body),
            'copy_text':copy_text,'headline_options':options,'missing_data':missing,'sources':srcs,
            'image_url':f'/media/render/{aid}.png' if exported else None,'status':a.get('status'),'source_url':a.get('source_url'),
            'image_source':a.get('image_source'),'image_license':a.get('image_license'),'image_author':a.get('image_author') or '',
            'image_kind':a.get('image_kind') or '','image_credit':photo_credit(a),
            'photo_download_url':f'/api/articles/{aid}/photo/file' if photo_ok else None,
            'ai_image_suggestion':a.get('ai_image_suggestion') or '','canva_url':design.get('url'),'canva_exported':exported,
            'carousel_slides':[f'/api/articles/{aid}/carousel/slide/{r["slide_index"]}' for r in carousel if r.get('exported')],
            'carousel_download_url':f'/api/articles/{aid}/carousel/download' if carousel and all(r.get('exported') for r in carousel) else None,
            'auto_publish':False}

def photo_credit(a):
    """De dónde salió la foto (solo informativo)."""
    if (a.get('image_kind') or '')=='user_upload' or str(a.get('image_url') or '').startswith('upload:'): return 'Foto subida por ti'
    host=urlparse(a.get('image_source') or a.get('image_url') or '').hostname or ''
    return host.replace('www.','')

@app.get('/api/articles/{aid}/photo/file',dependencies=[Depends(require_auth)])
def photo_file(aid:int):
    a=db.row('SELECT image_local FROM articles WHERE id=?',(aid,))
    if not a or not a.get('image_local') or not Path(a['image_local']).is_file(): raise HTTPException(404,'No hay foto seleccionada')
    p=Path(a['image_local'])
    if UPLOAD_DIR.resolve() not in p.resolve().parents: raise HTTPException(404)
    return FileResponse(p,media_type='image/jpeg',filename=f'infolinense-{aid}-foto.jpg')
@app.put('/api/articles/{aid}',dependencies=[Depends(require_auth)])
def edit_article(aid:int,body:EditArticle):
    original=db.row('SELECT * FROM articles WHERE id=?',(aid,))
    if not original: raise HTTPException(404)
    fields=[]; vals=[]
    for k,v in body.model_dump(exclude_none=True).items():
        if k=='section':
            v=layout.normalize_section(v)
        if k=='body':
            v=v[:2200]
            fields.append('social_text=?'); vals.append(v)
        fields.append(f'{k}=?'); vals.append(v)
    if fields:
        db.exec_(f"UPDATE articles SET {','.join(fields)},render_path=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",tuple(vals+[aid]))
        db.exec_('UPDATE canva_designs SET exported=0 WHERE article_id=?',(aid,))
        if original['status']=='approved':
            db.exec_("UPDATE articles SET status='review_ready' WHERE id=?",(aid,))
    return db.row('SELECT * FROM articles WHERE id=?',(aid,))
@app.get('/api/articles/{aid}/photos',dependencies=[Depends(require_auth)])
def article_photos(aid:int,refresh:bool=False,q:str=''):
    a=db.row('SELECT a.*,c.title source_title,c.url source_url,c.excerpt,c.source_id FROM articles a LEFT JOIN candidates c ON c.id=a.candidate_id WHERE a.id=?',(aid,))
    if not a: raise HTTPException(404)
    if q.strip():
        # Búsqueda libre en Wikimedia Commons (licencias libres documentadas).
        try: cached=json.loads(a.get('image_candidates_json') or '[]')
        except Exception: cached=[]
        found=photos.search_photos(q.strip()[:120])
        known={x.get('url') for x in cached}
        merged=cached+[x for x in found if x.get('url') not in known]
        db.exec_('UPDATE articles SET image_candidates_json=? WHERE id=?',(json.dumps(merged,ensure_ascii=False),aid))
        return found
    if not refresh:
        try: cached=json.loads(a.get('image_candidates_json') or '[]')
        except Exception: cached=[]
        if cached: return cached
    hint=(db.row('SELECT image_hint FROM candidates WHERE id=?',(a.get('candidate_id'),)) or {}).get('image_hint')
    cand={'title':a.get('headline') or a.get('source_title'),'url':a.get('source_url'),'image_hint':hint}
    imgs=photos.search_real_photos(cand,a.get('section') or 'CIUDAD',a.get('photo_query'))
    db.exec_('UPDATE articles SET image_candidates_json=? WHERE id=?',(json.dumps(imgs,ensure_ascii=False),aid)); return imgs
@app.post('/api/articles/{aid}/photo',dependencies=[Depends(require_auth)])
def choose_photo(aid:int,p:PhotoChoice):
    a=db.row('SELECT image_candidates_json FROM articles WHERE id=?',(aid,))
    if not a: raise HTTPException(404)
    url=p.url.strip()
    parsed=urlparse(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname: raise HTTPException(400,'La foto debe tener una URL web')
    try: allowed=json.loads(a.get('image_candidates_json') or '[]')
    except Exception: allowed=[]
    item=next((x for x in allowed if x.get('url')==url),None)
    if not item:
        item={'url':url,'source':p.source or url,'source_name':parsed.hostname or '','license':'','author':p.author,'kind':'web','publish_safe':True}
        allowed=allowed+[item]
    try: local=photos.download_image(url,min_width=300,min_height=200)
    except Exception as e: raise HTTPException(400,'No se pudo descargar la foto: '+str(e)[:200])
    db.exec_("UPDATE articles SET image_url=?,image_source=?,image_license=?,image_author=?,image_kind=?,image_local=?,image_candidates_json=?,render_path=NULL,ai_image_suggestion='',status=CASE WHEN status='approved' THEN 'review_ready' ELSE status END,updated_at=CURRENT_TIMESTAMP WHERE id=?",
             (url,item.get('source') or p.source,item.get('license') or p.license,item.get('author') or p.author,item.get('kind') or '',local,json.dumps(allowed,ensure_ascii=False),aid))
    db.exec_('UPDATE canva_designs SET exported=0 WHERE article_id=?',(aid,))
    return {'ok':True}

@app.post('/api/articles/{aid}/photo/auto',dependencies=[Depends(require_auth)])
def auto_photo(aid:int):
    """Busca fotos en internet y pone automáticamente la primera que se pueda usar."""
    a=db.row('SELECT a.*,c.title source_title,c.url source_url,c.source_id,c.image_hint FROM articles a LEFT JOIN candidates c ON c.id=a.candidate_id WHERE a.id=?',(aid,))
    if not a: raise HTTPException(404)
    cand={'title':a.get('headline') or a.get('source_title'),'url':a.get('source_url'),'image_hint':a.get('image_hint')}
    query=a.get('photo_query') or ''
    if not query and ai.AI_ENABLED and ai.ACTIVE_PROVIDER!='gemini':  # en el plan gratis no se gasta una petición en esto
        try:
            query=ai.photo_query(a)
            db.exec_('UPDATE articles SET photo_query=? WHERE id=?',(query,aid))
        except ai.AIProviderError: query=''
    imgs=photos.search_real_photos(cand,a.get('section') or '',query)
    try: previous=json.loads(a.get('image_candidates_json') or '[]')
    except Exception: previous=[]
    known={x.get('url') for x in imgs}
    imgs=imgs+[x for x in previous if x.get('url') not in known and not str(x.get('url','')).startswith('upload:')]
    chosen,local=photos.first_usable(imgs)
    db.exec_('UPDATE articles SET image_candidates_json=? WHERE id=?',(json.dumps(imgs,ensure_ascii=False),aid))
    if not chosen: raise HTTPException(404,'No se encontró ninguna foto en internet. Prueba con otra búsqueda o sube una.')
    db.exec_("UPDATE articles SET image_url=?,image_source=?,image_license='',image_author=?,image_kind=?,image_local=?,render_path=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
             (chosen['url'],chosen.get('source') or '',chosen.get('author') or '',chosen.get('kind') or '',local,aid))
    db.exec_('UPDATE canva_designs SET exported=0 WHERE article_id=?',(aid,))
    return {'ok':True,'photo':chosen,'found':len(imgs)}

@app.get('/api/photos/check',dependencies=[Depends(require_auth)])
def photos_check():
    return {'providers':photos.providers_check()}

@app.post('/api/articles/{aid}/photo/upload',dependencies=[Depends(require_auth)])
async def upload_article_photo(aid:int,file:UploadFile=File(...),license:str=Form(''),source:str=Form('Fotografía propia'),author:str=Form('')):
    if not db.row('SELECT id FROM articles WHERE id=?',(aid,)): raise HTTPException(404)
    source=source or 'Fotografía propia'
    data=await file.read(20_000_001)
    if len(data)>20_000_000: raise HTTPException(400,'La fotografía debe pesar menos de 20 MB')
    try:
        from PIL import Image
        from uuid import uuid4
        im=Image.open(BytesIO(data))
        if im.format not in ('JPEG','PNG','WEBP'): raise ValueError('Formato no permitido')
        if im.width*im.height>40_000_000: raise ValueError('La imagen tiene demasiados píxeles')
        im.load()
        dest=UPLOAD_DIR/(uuid4().hex+'.jpg')
        im.convert('RGB').save(dest,'JPEG',quality=92)
    except Exception as e: raise HTTPException(400,'No se pudo leer la fotografía: '+str(e))
    selected={'url':'upload:'+dest.stem,'source':source.strip()[:180],'license':license.strip()[:180],
              'author':author.strip()[:120],'kind':'user_upload','publish_safe':True}
    try: previous=json.loads((db.row('SELECT image_candidates_json FROM articles WHERE id=?',(aid,)) or {}).get('image_candidates_json') or '[]')
    except Exception: previous=[]
    db.exec_("UPDATE articles SET image_url=?,image_source=?,image_license=?,image_author=?,image_kind='user_upload',image_local=?,image_candidates_json=?,render_path=NULL,status=CASE WHEN status='approved' THEN 'review_ready' ELSE status END,updated_at=CURRENT_TIMESTAMP WHERE id=?",
             (selected['url'],selected['source'],selected['license'],selected['author'],str(dest),json.dumps(previous+[selected],ensure_ascii=False),aid))
    db.exec_('UPDATE canva_designs SET exported=0 WHERE article_id=?',(aid,))
    return {'ok':True,'photo':selected}
@app.post('/api/articles/{aid}/render',dependencies=[Depends(require_auth)])
def rerender(aid:int):
    artwork=db.row('SELECT exported FROM canva_designs WHERE article_id=?',(aid,))
    if artwork and artwork['exported']:
        p=RENDER_DIR/f'article_{aid}.png'
        if p.is_file(): return {'path':str(p),'url':f'/media/render/{aid}.png'}
    raise HTTPException(409,'Crea o vuelve a exportar el diseño en Canva antes de obtener el PNG')
@app.post('/api/articles/{aid}/approve',dependencies=[Depends(require_auth)])
def approve(aid:int):
    a=db.row('SELECT * FROM articles WHERE id=?',(aid,))
    if not a: raise HTTPException(404)
    if a['status']!='review_ready': raise HTTPException(400,'Solo se puede aprobar una pieza en revisión')
    if not a.get('image_local') or not Path(a['image_local']).is_file():
        raise HTTPException(400,'Elige una foto antes de aprobar')
    artwork=db.row('SELECT exported FROM canva_designs WHERE article_id=?',(aid,))
    if not canva.ready() or not canva.connected() or not artwork or not artwork['exported'] or not (RENDER_DIR/f'article_{aid}.png').is_file():
        raise HTTPException(400,'Crea y exporta primero el diseño final con la plantilla de Canva')
    db.exec_("UPDATE articles SET status='approved',updated_at=CURRENT_TIMESTAMP WHERE id=?",(aid,))
    return {'ok':True}
@app.post('/api/articles/{aid}/reject',dependencies=[Depends(require_auth)])
def reject(aid:int): db.exec_("UPDATE articles SET status='rejected',updated_at=CURRENT_TIMESTAMP WHERE id=?",(aid,)); return {'ok':True}
@app.post('/api/articles/{aid}/research-more',dependencies=[Depends(require_auth)])
def research_more(aid:int):
    a=db.row('SELECT candidate_id FROM articles WHERE id=?',(aid,))
    if not a: raise HTTPException(404)
    db.exec_('DELETE FROM articles WHERE id=?',(aid,)); db.exec_("UPDATE candidates SET status='new' WHERE id=?",(a['candidate_id'],))
    new_aid=pipeline.process_candidate(a['candidate_id'],force_research=True)
    return db.row('SELECT * FROM articles WHERE id=?',(new_aid,))
@app.post('/api/articles/{aid}/publish',dependencies=[Depends(require_auth)])
def publish(aid:int):
    """«Aprobar y publicar»: aprueba la noticia y la publica en la web (Lovable) si está configurada."""
    a=db.row('SELECT a.*,c.url source_url,c.source_name,c.outlet FROM articles a LEFT JOIN candidates c ON c.id=a.candidate_id WHERE a.id=?',(aid,))
    if not a: raise HTTPException(404)
    if a['status']=='published': return {'ok':True,'url':a.get('publish_url') or '','published':True}
    if not (a.get('headline') or '').strip() or not (a.get('body') or '').strip(): raise HTTPException(400,'Falta el titular o el texto')
    if not a.get('image_local') or not Path(a['image_local']).is_file(): raise HTTPException(400,'Elige una foto antes de publicar')
    db.exec_("UPDATE articles SET status='approved',updated_at=CURRENT_TIMESTAMP WHERE id=?",(aid,))
    db.exec_("UPDATE candidates SET status='review_ready' WHERE id=?",(a['candidate_id'],))
    if PUBLISH_MODE=='none' or not AUTO_PUBLISH:
        return {'ok':True,'published':False,'message':'Aprobada. La publicación en la web no está configurada en Railway.'}
    try:
        url=publishers.publish(a)
    except Exception as e:
        raise HTTPException(502,'Aprobada, pero la web no aceptó la noticia: '+str(e)[:250])
    db.exec_("UPDATE articles SET status='published',publish_url=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(url,aid))
    db.log('publish',f'Noticia {aid} publicada en la web')
    db.exec_("UPDATE candidates SET status='published' WHERE id=?",(a['candidate_id'],))
    return {'ok':True,'url':url,'published':True}
@app.get('/api/sources',dependencies=[Depends(require_auth)])
def list_sources(): return db.rows('SELECT * FROM sources ORDER BY priority DESC')
def validate_source(s):
    url=urlparse(s.url.strip())
    host=(url.hostname or '').lower()
    if url.scheme!='https' or not host or url.username or url.password or host in {'localhost','localhost.localdomain'} or host.endswith(('.local','.internal')):
        raise HTTPException(400,'La fuente debe tener una URL HTTPS pública')
    try:
        if not ipaddress.ip_address(host).is_global: raise HTTPException(400,'No se permiten direcciones privadas')
    except ValueError: pass
    if s.kind not in ('rss','html','bop','procurement','social','edictos') or not 0<=s.priority<=100 or not s.name.strip() or len(s.name)>120:
        raise HTTPException(400,'Revisa el nombre, tipo y prioridad de la fuente')
    if s.kind=='social' and host not in ('facebook.com','www.facebook.com','instagram.com','www.instagram.com'):
        raise HTTPException(400,'Las fuentes sociales aceptan páginas de Facebook o Instagram')
    if s.kind=='social' and not url.path.strip('/'):
        raise HTTPException(400,'Indica la página o cuenta pública')
    if s.kind=='procurement' and host!='contratos.gobierto.es':
        raise HTTPException(400,'El extractor de licitaciones corresponde a Gobierto')
    if s.kind=='bop' and host not in ('bopcadiz.es','www.bopcadiz.es'):
        raise HTTPException(400,'El extractor del BOP corresponde a Cádiz')
    return s.name.strip(),s.url.strip(),s.kind,s.priority,int(s.official),int(s.local_scope)
@app.post('/api/sources',dependencies=[Depends(require_auth)])
def add_source(s:SourceIn):
    values=validate_source(s)
    try: return {'id':db.exec_('INSERT INTO sources(name,url,kind,priority,official,local_scope) VALUES(?,?,?,?,?,?)',values)}
    except Exception as e: raise HTTPException(400,str(e))
@app.put('/api/sources/{sid}',dependencies=[Depends(require_auth)])
def edit_source(sid:int,s:SourceIn):
    if not db.row('SELECT id FROM sources WHERE id=?',(sid,)): raise HTTPException(404)
    values=validate_source(s)
    try:
        db.exec_('''UPDATE sources SET name=?,url=?,kind=?,priority=?,official=?,local_scope=?,
                   last_checked_at=NULL,last_error=NULL WHERE id=?''',(*values,sid))
    except Exception as e: raise HTTPException(400,str(e))
    return {'ok':True}
@app.post('/api/sources/{sid}/toggle',dependencies=[Depends(require_auth)])
def toggle_source(sid:int):
    if not db.row('SELECT id FROM sources WHERE id=?',(sid,)): raise HTTPException(404)
    db.exec_('UPDATE sources SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?',(sid,))
    return {'ok':True}
@app.post('/api/sources/{sid}/check',dependencies=[Depends(require_auth)])
def check_source(sid:int):
    if not db.row('SELECT id FROM sources WHERE id=?',(sid,)): raise HTTPException(404)
    return sources.probe_source(sid)
@app.get('/api/templates',dependencies=[Depends(require_auth)])
def list_templates():
    return [{'id':canva.TEMPLATE_ID,'name':'Plantilla de noticias Canva','format':'4:5','active':bool(canva.ready() and canva.connected()),'type':'canva','url':'https://www.canva.com/brand/brand-templates/'+canva.TEMPLATE_ID}] if canva.TEMPLATE_ID else []
@app.post('/api/templates/{tid}/activate',dependencies=[Depends(require_auth)])
def activate_template(tid:str):
    raise HTTPException(410,'La única plantilla es la de Canva; se administra en Canva')
@app.post('/api/templates/upload',dependencies=[Depends(require_auth)])
async def upload_template():
    raise HTTPException(410,'Las plantillas se editan en Canva; no se admiten archivos PPTX')

@app.get('/media/render/{aid}.png',dependencies=[Depends(require_auth)])
def render_file(aid:int):
    p=RENDER_DIR/f'article_{aid}.png'
    exported=db.row('SELECT exported FROM canva_designs WHERE article_id=?',(aid,))
    if not p.is_file() or not exported or not exported['exported']: raise HTTPException(404,'Exporta primero el diseño en Canva')
    return FileResponse(p,media_type='image/png',headers={'Cache-Control':'no-store'})
app.mount('/static',StaticFiles(directory=BASE_DIR/'static'),name='static')
@app.get('/')
def index(): return FileResponse(BASE_DIR/'static'/'index.html')

def scheduler_loop():
    time.sleep(8)
    while True:
        try:
            r=sources.scan_all()
            if not r.get('busy'):
                if AUTO_PIPELINE: pipeline.auto_process(r['added'])
                try: social.scan()
                except Exception as e: db.log('social_error',str(e)[:250])
                planner.rebuild_schedule()
                pipeline.queue_useful_pending()
        except Exception as e: db.log('scheduler_error',str(e)[:250])
        time.sleep(max(15,SCAN_INTERVAL_MINUTES)*60)
@app.on_event('startup')
def startup():
    if os.getenv('DISABLE_SCHEDULER','false').lower() not in {'1','true','yes'}:
        threading.Thread(target=scheduler_loop,daemon=True).start()
        try: pipeline.queue_useful_pending()
        except Exception as e: db.log('auto_draft_error',str(e)[:250])
