"""Área Campo de Gibraltar Desk — API y panel."""
import io
import json
import mimetypes
import re
import threading
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import ai, canva, db, docs, efemerides, photos, pipeline, redaccion, sources
from .ai import AIProviderError
from .auth import login, require_auth
from .config import (ADMIN_PASSWORD, CORS_ORIGINS, JWT_SECRET, SCAN_INTERVAL_MINUTES, UPLOAD_DIR, DOCS_DIR, RENDER_DIR,
                     PUBLIC_BASE_URL)

STATIC = Path(__file__).resolve().parent.parent / 'static'


@asynccontextmanager
async def lifespan(_app):
    db.init_db()
    sources.backfill()
    pipeline.start(SCAN_INTERVAL_MINUTES)
    yield


app = FastAPI(title='Área Campo de Gibraltar Desk', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=False, allow_methods=['*'], allow_headers=['*'])
app.mount('/static', StaticFiles(directory=STATIC), name='static')
AUTH = [Depends(require_auth)]
_scan_state = {'running': False, 'last': None, 'started_at': None}


@app.exception_handler(AIProviderError)
def _ai_error(_, exc: AIProviderError):
    return JSONResponse({'detail': str(exc), 'error': exc.as_dict()}, status_code=502 if exc.kind != 'config' else 400)


def _bad(exc):
    raise HTTPException(400, str(exc))


def _json(value, default):
    try:
        out = json.loads(value or '')
        return default if out is None else out
    except Exception:
        return default


# ---------------------------------------------------------------- sesión y estado

@app.post('/api/auth/login')
def api_login(body: dict):
    return {'token': login(str(body.get('password') or ''))}


@app.get('/api/health')
def health():
    return {'ok': True, 'app': 'area-desk', 'auth_configured': bool(ADMIN_PASSWORD and JWT_SECRET),
            'ai_provider': ai.ACTIVE_PROVIDER or None, 'draft_mode': 'ai' if ai.AI_ENABLED else 'source_draft',
            'canva_ready': canva.ready(), 'canva_connected': canva.connected() if canva.ready() else False}


@app.get('/api/ai/check', dependencies=AUTH)
def ai_check():
    return ai.check_providers()


# ---------------------------------------------------------------- búsqueda

def _scan_job():
    try:
        _scan_state['last'] = sources.scan_all()
        if datetime.now(pipeline.MADRID).hour >= 7:
            pipeline.daily_pick()
        pipeline.queue_pending()
    except Exception as exc:
        _scan_state['last'] = {'added': [], 'errors': [str(exc)[:300]]}
    finally:
        _scan_state['running'] = False


@app.post('/api/scan', dependencies=AUTH)
def scan():
    if _scan_state['running']:
        return {'started': False, 'running': True}
    _scan_state.update(running=True, started_at=datetime.now().isoformat())
    threading.Thread(target=_scan_job, daemon=True).start()
    return {'started': True, 'running': True}


@app.get('/api/scan', dependencies=AUTH)
def scan_status():
    last = _scan_state['last'] or {}
    return {'running': _scan_state['running'], 'started_at': _scan_state['started_at'],
            'added': len(last.get('added') or []), 'errors': last.get('errors') or []}


# ---------------------------------------------------------------- propuestas

def _cand_out(rows):
    links = sources.links_for([r['id'] for r in rows])
    for r in rows:
        r['links'] = links.get(r['id'], [])
        r['competitors'] = _json(r.pop('competitors_json', None), [])
        r['docs'] = _json(r.pop('docs_json', None), [])
        r['group'] = r.get('block') or 'Instituciones'
    return rows


@app.get('/api/workflow', dependencies=AUTH)
def workflow():
    n = lambda sql: db.row(sql)['n']
    return {'sort': n("SELECT COUNT(*) n FROM candidates WHERE status='new' AND priority IN ('undecided','')"),
            'in_area': n("SELECT COUNT(*) n FROM candidates WHERE status='in_area'"),
            'writing': n("SELECT COUNT(*) n FROM candidates WHERE work_state IN ('queued','working','error')"),
            'review': n("SELECT COUNT(*) n FROM articles WHERE status='draft'"),
            'ready': n("SELECT COUNT(*) n FROM articles WHERE status='ready'")}


@app.get('/api/today', dependencies=AUTH)
def today():
    pick = pipeline.today_pick()
    art = pipeline.article_for(pick['id']) if pick else None
    return {'pick': _cand_out([pick])[0] if pick else None, 'article_id': art['id'] if art else None,
            'suggestion': (_cand_out([pipeline.best_today()])[0] if not pick and pipeline.best_today() else None),
            'efemerides': efemerides.upcoming(14)}


@app.post('/api/today', dependencies=AUTH)
def today_new():
    pick = pipeline.daily_pick(force=True)
    if not pick:
        raise HTTPException(404, 'No hay ninguna propuesta reciente que no esté ya en Área. Pulsa Buscar.')
    return {'pick': pick}


@app.get('/api/sort-queue', dependencies=AUTH)
def sort_queue(area: int = 0):
    if area:
        rows = db.rows("SELECT * FROM candidates WHERE status='in_area' ORDER BY published_at DESC LIMIT 150")
    else:
        rows = db.rows("""SELECT * FROM candidates WHERE status='new' AND priority IN ('undecided','')
                          ORDER BY exclusive DESC, score DESC, published_at DESC LIMIT 400""")
    return _cand_out(rows)


TSTATUS = {'PUB': 'En plazo', 'PRE': 'Anuncio previo', 'EV': 'En evaluación', 'ADJ': 'Adjudicada', 'RES': 'Formalizada',
           'MENOR': 'Contrato menor', 'ANUL': 'Anulada / desierta'}


@app.get('/api/tenders', dependencies=AUTH)
def tenders(town: str = '', status: str = '', sort: str = 'new', days: int = 30, q: str = '', area: int = 0):
    """Todas las licitaciones de la comarca, con filtros por municipio y fase y recuentos para los chips."""
    since = (datetime.utcnow() - timedelta(days=max(1, min(days, 120)))).isoformat()
    rows = db.rows("""SELECT * FROM candidates WHERE block='Licitaciones' AND status IN ('new','in_area','chosen')
                       AND priority!='no' AND (published_at>=? OR published_at='') ORDER BY published_at DESC LIMIT 1500""", (since,))
    in_area = sum(1 for r in rows if r['status'] == 'in_area')
    # Lo que Diario Área ya ha publicado no se propone (solo se ve si se pide expresamente)
    rows = [r for r in rows if (r['status'] == 'in_area') == bool(area)]
    for r in rows:
        r['town'] = (r.get('towns') or '').split(',')[0] or 'Campo de Gibraltar'
        r['tstatus'] = r.get('tstatus') or 'PUB'
    counts_town, counts_status = {}, {}
    for r in rows:
        if not status or r['tstatus'] == status:
            counts_town[r['town']] = counts_town.get(r['town'], 0) + 1
        if not town or r['town'] == town:
            counts_status[r['tstatus']] = counts_status.get(r['tstatus'], 0) + 1
    if town:
        rows = [r for r in rows if r['town'] == town]
    if status:
        rows = [r for r in rows if r['tstatus'] == status]
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in ((r.get('title') or '') + ' ' + (r.get('excerpt') or '') + ' ' + (r.get('organism') or '')).lower()]
    if sort == 'amount':
        rows.sort(key=lambda r: r.get('amount_value') or 0, reverse=True)
    elif sort == 'deadline':
        rows.sort(key=lambda r: r.get('deadline') or '9999')
    elif sort == 'score':
        rows.sort(key=lambda r: (r['exclusive'], r['score']), reverse=True)
    out = _cand_out(rows[:300])
    for r in out:
        a = pipeline.article_for(r['id'])
        r['article_id'] = a['id'] if a else None
    order = ['Algeciras', 'La Línea', 'San Roque', 'Los Barrios', 'Tarifa', 'Jimena', 'Castellar', 'San Martín del Tesorillo', 'Campo de Gibraltar']
    return {'items': out, 'total': len(rows), 'towns': [{'town': t, 'n': counts_town.get(t, 0)} for t in order],
            'statuses': [{'code': k, 'label': v, 'n': counts_status.get(k, 0)} for k, v in TSTATUS.items()],
            'sum': round(sum(r.get('amount_value') or 0 for r in rows), 2), 'in_area': in_area}


@app.post('/api/tenders/summary', dependencies=AUTH)
def tenders_summary(body: dict):
    """Pieza resumen («Las licitaciones de la semana en…») a partir de varias licitaciones marcadas."""
    ids = [int(i) for i in (body.get('ids') or []) if str(i).isdigit()][:12]
    if len(ids) < 2:
        raise HTTPException(400, 'Marca al menos dos licitaciones')
    rows = db.rows('SELECT * FROM candidates WHERE id IN (%s)' % ','.join('?' * len(ids)), tuple(ids))
    towns = sorted({(r.get('towns') or '').split(',')[0] for r in rows if r.get('towns')})
    where = towns[0] if len(towns) == 1 else 'el Campo de Gibraltar'
    total = sum(r.get('amount_value') or 0 for r in rows)
    title = str(body.get('title') or '').strip() or 'Resumen: %s licitaciones en %s (%s)' % (len(rows), where, sources._euros(total) or 's/i')
    lines = ['- [%s] %s · %s · %s%s' % (TSTATUS.get(r.get('tstatus') or 'PUB', ''), re.sub(r'^[^:]{3,30}:\s*', '', r['title']),
                                       r.get('organism') or '', r.get('amount') or 'importe sin indicar',
                                       (' · plazo ' + r['deadline']) if r.get('deadline') else '') for r in rows]
    url = 'resumen://%s/%s' % (datetime.now().strftime('%Y%m%d%H%M%S'), '-'.join(map(str, ids)))
    cid = db.exec_("""INSERT INTO candidates(source_name,title,url,published_at,excerpt,block,tag,towns,status,priority,tstatus,amount_value,docs_json)
                      VALUES('Resumen de licitaciones',?,?,?,?, 'Licitaciones','licitacion',?,'new','today','RESUMEN',?,?)""",
                   (title, url, datetime.utcnow().isoformat() + '+00:00', '\n'.join(lines), ','.join(towns), total,
                    json.dumps([d for r in rows for d in _json(r.get('docs_json'), [])][:10], ensure_ascii=False)))
    for r in rows:
        db.exec_('INSERT OR IGNORE INTO candidate_links(candidate_id,source_name,outlet,url,title,excerpt,published_at) VALUES(?,?,?,?,?,?,?)',
                 (cid, r.get('source_name'), r.get('organism'), r['url'], r['title'], r.get('excerpt'), r.get('published_at')))
        db.exec_("UPDATE candidates SET status='chosen' WHERE id=?", (r['id'],))
    pipeline.queue_write(cid, 'urgent')
    return {'candidate_id': cid, 'queued': True}


@app.post('/api/candidates/{cid}/triage', dependencies=AUTH)
def triage(cid: int, body: dict):
    p = str(body.get('priority') or '')
    if p not in ('urgent', 'today', 'week', 'later', 'no', 'undecided'):
        raise HTTPException(400, 'Prioridad desconocida')
    if p == 'no':
        db.exec_("UPDATE candidates SET priority='no',status='archived' WHERE id=?", (cid,))
        return {'ok': True}
    db.exec_("UPDATE candidates SET priority=?,status=CASE WHEN status IN ('in_area','archived') THEN 'new' ELSE status END WHERE id=?", (p, cid))
    queued = False
    if p in ('urgent', 'today', 'week', 'later') and not pipeline.article_for(cid):
        queued = pipeline.queue_write(cid, p)
    return {'ok': True, 'queued': queued}


@app.post('/api/candidates/{cid}/write', dependencies=AUTH)
def write(cid: int):
    if not db.row('SELECT 1 FROM candidates WHERE id=?', (cid,)):
        raise HTTPException(404, 'Propuesta no encontrada')
    db.exec_("UPDATE candidates SET work_state=NULL WHERE id=? AND work_state='error'", (cid,))
    return {'queued': pipeline.queue_write(cid, 'urgent')}


@app.get('/api/drafting', dependencies=AUTH)
def drafting():
    rows = db.rows("""SELECT * FROM candidates WHERE work_state IN ('queued','working','error')
                      OR (work_state='done' AND work_updated_at>=datetime('now','-30 minutes')) ORDER BY work_updated_at DESC""")
    out = _cand_out(rows)
    for r in out:
        a = pipeline.article_for(r['id'])
        r['article_id'] = a['id'] if a else None
    return out


# ---------------------------------------------------------------- artículos

def _art_list(status):
    rows = db.rows('''SELECT a.*,c.title cand_title,c.block,c.tag,c.exclusive,c.area_state,c.priority,c.url source_url,c.source_name
                      FROM articles a JOIN candidates c ON c.id=a.candidate_id WHERE a.status=? ORDER BY a.updated_at DESC''', (status,))
    for r in rows:
        r['slides'] = _json(r.pop('slides_json', None), [])
        r['cover'] = db.row("SELECT id,local_path,url FROM media WHERE article_id=? AND selected=1 ORDER BY CASE kind WHEN 'canva' THEN 0 ELSE 1 END, slide, id LIMIT 1", (r['id'],))
    return rows


@app.get('/api/review', dependencies=AUTH)
def review():
    return _art_list('draft')


@app.get('/api/ready', dependencies=AUTH)
def ready_list():
    return _art_list('ready') + _art_list('published')[:20]


def _article(aid):
    a = db.row('SELECT * FROM articles WHERE id=?', (aid,))
    if not a:
        raise HTTPException(404, 'Noticia no encontrada')
    return a


@app.get('/api/articles/{aid}', dependencies=AUTH)
def article(aid: int):
    a = _article(aid)
    c = _cand_out([db.row('SELECT * FROM candidates WHERE id=?', (a['candidate_id'],))])[0]
    for k, d in (('headline_options_json', []), ('slides_json', []), ('render_json', {}), ('sources_json', {})):
        a[k.replace('_json', '')] = _json(a.pop(k, None), d)
    a['media'] = db.rows('SELECT * FROM media WHERE article_id=? ORDER BY CASE kind WHEN \'carrusel\' THEN -1 WHEN \'canva\' THEN 0 WHEN \'doc\' THEN 1 WHEN \'plano\' THEN 2 '
                         'WHEN \'subida\' THEN 3 WHEN \'fuente\' THEN 4 WHEN \'medio\' THEN 5 ELSE 6 END, slide, id', (aid,))
    a['docs_status'] = _json(db.setting('docs_%s' % aid), None)
    a['candidate'] = c
    return a


@app.put('/api/articles/{aid}', dependencies=AUTH)
def update_article(aid: int, body: dict):
    _article(aid)
    simple = {'headline': 300, 'entradilla': 600, 'body': 6000, 'instagram_copy': 4000, 'section': 40, 'town': 60, 'photo_query': 160,
              'published_url': 500}
    for k, limit in simple.items():
        if k in body:
            db.exec_('UPDATE articles SET %s=?,updated_at=CURRENT_TIMESTAMP WHERE id=?' % k, (str(body[k] or '')[:limit], aid))
    if 'slides' in body and isinstance(body['slides'], list):
        db.exec_('UPDATE articles SET slides_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                 (json.dumps(redaccion._slides(body['slides']), ensure_ascii=False), aid))
    if 'render' in body and isinstance(body['render'], dict):
        db.exec_('UPDATE articles SET render_json=? WHERE id=?', (json.dumps(body['render'], ensure_ascii=False), aid))
    return article(aid)


@app.post('/api/articles/{aid}/regenerate', dependencies=AUTH)
def regenerate(aid: int, body: dict):
    a = _article(aid)
    out = redaccion.regenerate(a, str(body.get('what') or ''), str(body.get('extra') or '')[:500])
    if 'headline_options' in out:
        db.exec_('UPDATE articles SET headline_options_json=? WHERE id=?', (json.dumps(out['headline_options'], ensure_ascii=False), aid))
    if 'slides' in out:
        db.exec_('UPDATE articles SET slides_json=? WHERE id=?', (json.dumps(out['slides'], ensure_ascii=False), aid))
    if 'instagram_copy' in out:
        db.exec_('UPDATE articles SET instagram_copy=? WHERE id=?', (out['instagram_copy'], aid))
    if 'render' in out:
        db.exec_('UPDATE articles SET render_json=? WHERE id=?', (json.dumps(out['render'], ensure_ascii=False), aid))
    return article(aid)


@app.post('/api/articles/{aid}/rewrite', dependencies=AUTH)
def rewrite(aid: int):
    a = _article(aid)
    db.exec_("UPDATE candidates SET work_state=NULL WHERE id=?", (a['candidate_id'],))
    return {'queued': pipeline.queue_write(a['candidate_id'], 'urgent')}


@app.post('/api/articles/{aid}/status', dependencies=AUTH)
def set_status(aid: int, body: dict):
    _article(aid)
    st = str(body.get('status') or '')
    if st not in ('draft', 'ready', 'published', 'rejected'):
        raise HTTPException(400, 'Estado desconocido')
    db.exec_('UPDATE articles SET status=?,published_url=COALESCE(?,published_url),updated_at=CURRENT_TIMESTAMP WHERE id=?',
             (st, body.get('published_url'), aid))
    if st == 'rejected':
        db.exec_("UPDATE candidates SET status='archived',priority='no' WHERE id=(SELECT candidate_id FROM articles WHERE id=?)", (aid,))
    return {'ok': True}


# ---------------------------------------------------------------- imágenes

@app.post('/api/articles/{aid}/images/search', dependencies=AUTH)
def image_search(aid: int, body: dict):
    _article(aid)
    q = str(body.get('q') or '').strip()
    if not q:
        raise HTTPException(400, 'Escribe qué buscar')
    found = photos.internet_images(q, limit=20)
    for it in found:
        pipeline._add_media(aid, 'internet', it['url'], it['source'], it.get('source_name') or '')
    return article(aid)


@app.post('/api/articles/{aid}/images/docs', dependencies=AUTH)
def image_docs(aid: int):
    _article(aid)
    db.set_setting('docs_%s' % aid, json.dumps({'running': True}))
    threading.Thread(target=pipeline._docs_bg, args=(aid,), daemon=True).start()
    return {'running': True}


@app.post('/api/articles/{aid}/media/{mid}', dependencies=AUTH)
def media_update(aid: int, mid: int, body: dict):
    m = db.row('SELECT * FROM media WHERE id=? AND article_id=?', (mid, aid))
    if not m:
        raise HTTPException(404, 'Imagen no encontrada')
    if 'selected' in body:
        db.exec_('UPDATE media SET selected=? WHERE id=?', (1 if body['selected'] else 0, mid))
    if 'slide' in body:
        db.exec_('UPDATE media SET slide=? WHERE id=?', (int(body['slide']) if body['slide'] not in (None, '') else None, mid))
    if body.get('delete'):
        db.exec_('DELETE FROM media WHERE id=?', (mid,))
    return {'ok': True}


@app.post('/api/articles/{aid}/media', dependencies=AUTH)
async def media_upload(aid: int, file: UploadFile = File(...)):
    _article(aid)
    data = await file.read()
    if len(data) > 25_000_000:
        raise HTTPException(400, 'La imagen supera 25 MB')
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data)).convert('RGB')
    except Exception:
        raise HTTPException(400, 'No es una imagen válida')
    folder = UPLOAD_DIR / str(aid)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ('%s.jpg' % datetime.now().strftime('%Y%m%d%H%M%S%f'))
    im.save(path, 'JPEG', quality=93)
    db.exec_("INSERT INTO media(article_id,kind,local_path,width,height,caption,selected) VALUES(?,?,?,?,?,?,1)",
             (aid, 'subida', str(path), im.width, im.height, file.filename or 'Subida'))
    return article(aid)


def _local(m):
    """Ruta local de una imagen; las de internet se descargan la primera vez."""
    p = m.get('local_path') or ''
    if p and Path(p).is_file():
        return p
    if not m.get('url'):
        raise HTTPException(404, 'Imagen no disponible')
    try:
        p = photos.download_image(m['url'], min_width=200, min_height=150)
    except Exception as exc:
        raise HTTPException(502, 'No se pudo descargar la imagen: %s' % str(exc)[:120])
    from PIL import Image
    with Image.open(p) as im:
        db.exec_('UPDATE media SET local_path=?,width=?,height=? WHERE id=?', (p, im.width, im.height, m['id']))
    return p


def _safe_path(p):
    rp = Path(p).resolve()
    for root in (UPLOAD_DIR, DOCS_DIR, RENDER_DIR):
        if str(rp).startswith(str(root.resolve())):
            return rp
    raise HTTPException(403, 'Ruta no permitida')


@app.get('/api/media/{mid}/file', dependencies=AUTH)
def media_file(mid: int):
    m = db.row('SELECT * FROM media WHERE id=?', (mid,))
    if not m:
        raise HTTPException(404, 'Imagen no encontrada')
    p = _safe_path(_local(m))
    return FileResponse(p, media_type=mimetypes.guess_type(str(p))[0] or 'image/jpeg',
                        filename='area_%s_%s%s' % (m['article_id'], mid, p.suffix))


def slide_text(i, s):
    clean = lambda t: re.sub(r'==|\+\+|\*\*', '', str(t or ''))
    out = ['%s. [%s]%s %s' % (i + 1, s.get('layout') or '', (' ' + clean(s['kicker']) + ' ·') if s.get('kicker') else '', clean(s.get('title')))]
    if s.get('text'):
        out.append(clean(s['text']))
    out += ['· ' + clean(b) for b in s.get('bullets') or []]
    if s.get('chips'):
        out.append(' → '.join(map(clean, s['chips'])))
    if s.get('figure') or s.get('status'):
        out.append(' '.join(x for x in [('Estado: ' + s['status']) if s.get('status') else '', s.get('figure') or '', clean(s.get('figure_label'))] if x))
    out += ['· %s %s %s' % (c.get('label') or '', c.get('figure') or '', clean(c.get('text'))) for c in s.get('cards') or []]
    if s.get('image_hint'):
        out.append('(Imagen: %s)' % s['image_hint'])
    return '\n'.join(out)


def _texts(a):
    slides = _json(a.get('slides_json'), [])
    render = _json(a.get('render_json'), {})
    parts = ['TITULAR\n' + (a.get('headline') or ''), 'ENTRADILLA\n' + (a.get('entradilla') or ''), 'TEXTO\n' + (a.get('body') or ''),
             'COPY DE INSTAGRAM\n' + (a.get('instagram_copy') or ''),
             'CARRUSEL\n' + '\n\n'.join(slide_text(i, s) for i, s in enumerate(slides))]
    if render.get('recommended'):
        parts.append('RENDER RECOMENDADO\n%s\n\n%s' % (render.get('why') or '', render.get('prompt') or ''))
    return '\n\n────────\n\n'.join(parts)


@app.get('/api/articles/{aid}/zip', dependencies=AUTH)
def article_zip(aid: int, all: int = 0):
    a = _article(aid)
    q = 'SELECT * FROM media WHERE article_id=?' + ('' if all else ' AND selected=1') + ' ORDER BY CASE kind WHEN \'carrusel\' THEN 0 WHEN \'canva\' THEN 1 ELSE 2 END, slide, id'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('textos.txt', _texts(a))
        for n, m in enumerate(db.rows(q, (aid,)), start=1):
            try:
                p = _safe_path(_local(m))
            except HTTPException:
                continue
            prefix = ('diapositiva_%02d' % (m['slide'] or n)) if m['kind'] in ('carrusel', 'canva') else m['kind']
            z.write(p, '%02d_%s%s' % (n, prefix, p.suffix))
    slug = re.sub(r'[^a-z0-9]+', '-', (a.get('headline') or 'area').lower())[:50].strip('-')
    return Response(buf.getvalue(), media_type='application/zip', headers={'Content-Disposition': 'attachment; filename="area-%s.zip"' % slug})


@app.post('/api/articles/{aid}/carousel/png', dependencies=AUTH)
def carousel_png(aid: int, body: dict):
    """Guarda las diapositivas generadas en el panel (PNG 1080×1350) como imágenes del carrusel."""
    import base64
    _article(aid)
    pngs = body.get('slides') or []
    if not pngs or len(pngs) > 12:
        raise HTTPException(400, 'Faltan las diapositivas')
    folder = RENDER_DIR / ('carrusel_%s' % aid)
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob('*.png'):
        old.unlink()
    db.exec_("DELETE FROM media WHERE article_id=? AND kind='carrusel'", (aid,))
    stamp = datetime.now().strftime('%H%M%S')
    for n, d in enumerate(pngs, start=1):
        m = re.match(r'^data:image/png;base64,(.+)$', str(d or ''), re.S)
        if not m:
            raise HTTPException(400, 'La diapositiva %s no es un PNG' % n)
        raw = base64.b64decode(m.group(1))
        if len(raw) > 15_000_000:
            raise HTTPException(400, 'La diapositiva %s es demasiado grande' % n)
        path = folder / ('%02d_%s.png' % (n, stamp))
        path.write_bytes(raw)
        db.exec_("INSERT INTO media(article_id,kind,local_path,width,height,caption,slide,selected) VALUES(?,?,?,?,?,?,?,1)",
                 (aid, 'carrusel', str(path), 1080, 1350, 'Diapositiva %s' % n, n))
    return article(aid)


# ---------------------------------------------------------------- logos de Área para las diapositivas

LOGO_DIR = UPLOAD_DIR / 'marca'


@app.get('/api/brand/logo/{kind}', dependencies=AUTH)
def brand_logo(kind: str):
    p = LOGO_DIR / ('logo_%s.png' % ('blanco' if kind == 'blanco' else 'color'))
    if not p.is_file():
        raise HTTPException(404, 'Sin logo')
    return FileResponse(p, media_type='image/png')


@app.post('/api/brand/logo/{kind}', dependencies=AUTH)
async def brand_logo_upload(kind: str, file: UploadFile = File(None)):
    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    p = LOGO_DIR / ('logo_%s.png' % ('blanco' if kind == 'blanco' else 'color'))
    if file is None:
        if p.exists():
            p.unlink()
        return {'ok': True, 'removed': True}
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(await file.read())).convert('RGBA')
    except Exception:
        raise HTTPException(400, 'No es una imagen válida (usa PNG con fondo transparente)')
    bbox = im.getbbox()
    if bbox:
        im = im.crop(bbox)
    im.thumbnail((1200, 600))
    im.save(p, 'PNG')
    return {'ok': True}


@app.get('/api/brand', dependencies=AUTH)
def brand():
    return {k: (LOGO_DIR / ('logo_%s.png' % k)).is_file() for k in ('blanco', 'color')}


# ---------------------------------------------------------------- Canva

@app.get('/api/canva/connect', dependencies=AUTH)
def canva_connect():
    try:
        return {'url': canva.authorization_url()}
    except ValueError as exc:
        _bad(exc)


@app.get('/api/canva/callback')
def canva_callback(code: str = '', state: str = '', error: str = ''):
    if error:
        return RedirectResponse('/#settings?canva=error')
    try:
        canva.complete(code, state)
    except ValueError as exc:
        return HTMLResponse('<p>%s</p><p><a href="/#settings">Volver</a></p>' % str(exc), status_code=400)
    return RedirectResponse('/#settings')


@app.get('/api/canva/template', dependencies=AUTH)
def canva_template():
    out = {'template_id': canva.template_id(), 'ready': canva.ready(), 'connected': canva.connected() if canva.ready() else False,
           'callback': canva.callback_url() if PUBLIC_BASE_URL else ''}
    if out['connected'] and out['template_id']:
        try:
            out['fields'] = canva.template_fields()
        except ValueError as exc:
            out['error'] = str(exc)
    return out


@app.put('/api/canva/template', dependencies=AUTH)
def canva_template_set(body: dict):
    tid = canva.template_from_link(str(body.get('template') or ''))
    if body.get('template') and not tid:
        raise HTTPException(400, 'No reconozco ese enlace. Pega el enlace de la plantilla de marca de Canva.')
    db.set_setting('canva_template', tid)
    return canva_template()


@app.post('/api/articles/{aid}/canva', dependencies=AUTH)
def article_canva(aid: int):
    a = _article(aid)
    slides = _json(a.get('slides_json'), [])
    media = db.rows("SELECT * FROM media WHERE article_id=? AND selected=1 AND kind!='canva' ORDER BY COALESCE(slide,99), id", (aid,))
    if not media:
        raise HTTPException(400, 'Marca al menos una imagen (con ✓) para el carrusel')
    by_slide = {m['slide']: m for m in media if m.get('slide')}
    loose = [m for m in media if not m.get('slide')] or media
    paths = []
    for i in range(len(slides)):
        m = by_slide.get(i + 1) or loose[i % len(loose)]
        paths.append(_local(m))
    try:
        res = canva.create_carousel(a, slides, paths)
    except ValueError as exc:
        db.exec_('UPDATE articles SET canva_error=? WHERE id=?', (str(exc)[:300], aid))
        _bad(exc)
    return {**res, 'article': article(aid)}


@app.post('/api/articles/{aid}/canva/export', dependencies=AUTH)
def article_canva_export(aid: int):
    a = _article(aid)
    if not a.get('canva_design_id'):
        raise HTTPException(400, 'Primero crea el carrusel en Canva')
    try:
        canva.export_carousel(aid, a['canva_design_id'], len(_json(a.get('slides_json'), [])), a.get('canva_url') or '')
    except ValueError as exc:
        _bad(exc)
    return article(aid)


# ---------------------------------------------------------------- fuentes, prensa y efemérides

@app.get('/api/sources', dependencies=AUTH)
def list_sources():
    return {'sources': db.rows('SELECT * FROM sources ORDER BY block, priority DESC'),
            'press': db.rows('SELECT ps.*,(SELECT COUNT(*) FROM press p WHERE p.role=ps.role) stored FROM press_sources ps ORDER BY role, name'),
            'blocks': sources.BLOCKS}


@app.post('/api/sources', dependencies=AUTH)
def add_source(body: dict):
    name, url = str(body.get('name') or '').strip(), str(body.get('url') or '').strip()
    if not name or not url.startswith('http'):
        raise HTTPException(400, 'Pon nombre y dirección (https://…)')
    kind = body.get('kind') if body.get('kind') in ('rss', 'html', 'placsp', 'gobierto', 'bop', 'edictos') else 'rss'
    block = body.get('block') if body.get('block') in sources.BLOCKS else 'Instituciones'
    if body.get('press'):
        db.exec_('INSERT OR IGNORE INTO press_sources(name,url,role) VALUES(?,?,?)', (name, url, 'area' if body.get('role') == 'area' else 'competitor'))
    else:
        db.exec_('INSERT OR IGNORE INTO sources(name,url,kind,block,priority,official) VALUES(?,?,?,?,?,?)',
                 (name, url, kind, block, int(body.get('priority') or 75), 1 if body.get('official') else 0))
    return list_sources()


@app.post('/api/sources/{sid}/toggle', dependencies=AUTH)
def toggle_source(sid: int, press: int = 0):
    table = 'press_sources' if press else 'sources'
    db.exec_('UPDATE %s SET active=1-active WHERE id=?' % table, (sid,))
    return list_sources()


@app.delete('/api/sources/{sid}', dependencies=AUTH)
def delete_source(sid: int, press: int = 0):
    db.exec_('DELETE FROM %s WHERE id=?' % ('press_sources' if press else 'sources'), (sid,))
    return list_sources()


@app.post('/api/sources/{sid}/check', dependencies=AUTH)
def check_source(sid: int):
    return sources.probe_source(sid)


@app.get('/api/efemerides', dependencies=AUTH)
def list_efemerides():
    return {'all': db.rows('SELECT * FROM efemerides ORDER BY month, day'), 'upcoming': efemerides.upcoming(30)}


@app.post('/api/efemerides', dependencies=AUTH)
def add_efemeride(body: dict):
    try:
        m, d = int(body.get('month')), int(body.get('day'))
        datetime(2024, m, d)
    except Exception:
        raise HTTPException(400, 'Fecha no válida')
    title = str(body.get('title') or '').strip()[:200]
    if not title:
        raise HTTPException(400, 'Escribe qué pasó')
    year = int(body['year']) if str(body.get('year') or '').isdigit() else None
    db.exec_('INSERT OR REPLACE INTO efemerides(month,day,year,title,note,towns,verified) VALUES(?,?,?,?,?,?,?)',
             (m, d, year, title, str(body.get('note') or '')[:600], str(body.get('towns') or '')[:120], 1 if body.get('verified') else 0))
    return list_efemerides()


@app.post('/api/efemerides/{eid}', dependencies=AUTH)
def edit_efemeride(eid: int, body: dict):
    if body.get('delete'):
        db.exec_('DELETE FROM efemerides WHERE id=?', (eid,))
    if 'verified' in body:
        db.exec_('UPDATE efemerides SET verified=? WHERE id=?', (1 if body['verified'] else 0, eid))
    if 'active' in body:
        db.exec_('UPDATE efemerides SET active=? WHERE id=?', (1 if body['active'] else 0, eid))
    return list_efemerides()


@app.get('/api/activity', dependencies=AUTH)
def activity():
    return db.rows('SELECT * FROM activity ORDER BY id DESC LIMIT 60')


# ---------------------------------------------------------------- panel

@app.get('/')
def index():
    return FileResponse(STATIC / 'index.html')
