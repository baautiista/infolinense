"""Redacción en segundo plano, imágenes y propuesta del día."""
import json
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db, sources, photos, docs, redaccion
from .ai import AIProviderError
from .config import AUTO_DRAFT, DAILY_PICK_HOUR

MADRID = ZoneInfo('Europe/Madrid')
_locks = {}
_queue = queue.PriorityQueue()
_queued = set()
_PRIO = {'urgent': 0, 'today': 1, 'week': 2, 'later': 3}


def _lock(cid):
    return _locks.setdefault(cid, threading.Lock())


def _work(cid, state, step=None, error=None):
    db.exec_('UPDATE candidates SET work_state=?,work_error=?,work_updated_at=CURRENT_TIMESTAMP WHERE id=?',
             (state, (error or step or '')[:500], cid))


def article_for(cid):
    return db.row("SELECT * FROM articles WHERE candidate_id=? AND status!='rejected'", (cid,))


def _docs_text(cand, limit=6000):
    """Texto del primer documento útil del expediente (memoria, pliego técnico…)."""
    try:
        links = docs.doc_links(cand)[:2]
    except Exception:
        return ''
    out = ''
    for d in links:
        out += '\n' + sources.fetch_article_text(d['url'])[:limit]
        if len(out) > limit:
            break
    return out[:limit]


def write_candidate(cid):
    """Redacta (o rehace) la pieza de una propuesta. Devuelve el artículo."""
    with _lock(cid):
        cand = db.row('SELECT * FROM candidates WHERE id=?', (cid,))
        if not cand:
            raise ValueError('Propuesta no encontrada')
        _work(cid, 'working', 'Leyendo la fuente')
        try:
            cand['_links'] = sources.links_for([cid]).get(cid, [])
            text = sources.fetch_article_text(cand['url'])
            for l in cand['_links'][:2]:
                if len(text) < 1500:
                    text += '\n\n' + sources.fetch_article_text(l['url'])
            dtext = _docs_text(cand) if cand.get('tag') in ('licitacion', 'edicto') or cand.get('docs_json') not in (None, '', '[]') else ''
            _work(cid, 'working', 'Redactando')
            try:
                data = redaccion.draft(cand, text, dtext)
            except AIProviderError as exc:
                if exc.kind not in ('auth', 'config', 'billing', 'quota', 'bad_request'):
                    raise
                # La IA no está disponible: se prepara el borrador desde la fuente para no bloquear la pieza
                data = redaccion.free_draft(cand, text)
                data['missing'] = ['Redactado SIN IA porque falló la IA: %s' % str(exc)[:300]] + data.get('missing', [])
                db.log('ai', 'Sin IA para %s: %s' % (cid, exc))
        except AIProviderError as exc:
            _work(cid, 'error', error=str(exc))
            raise
        except Exception as exc:
            _work(cid, 'error', error='No se pudo redactar: %s' % str(exc)[:200])
            raise
        fields = (data['section'], data['town'], data['focus'], data['headline'], data['entradilla'], data['body'], data['instagram_copy'],
                  json.dumps(data['headline_options'], ensure_ascii=False), json.dumps(data['slides'], ensure_ascii=False),
                  json.dumps(data['render'], ensure_ascii=False), data['photo_query'],
                  json.dumps({'missing': data['missing'], 'links': [{'url': cand['url'], 'name': cand.get('source_name')}] +
                              [{'url': l['url'], 'name': l.get('outlet') or l.get('source_name')} for l in cand['_links']]}, ensure_ascii=False),
                  (('Actualiza lo publicado en Área: «%s»' % cand.get('area_title')) if cand.get('area_state') == 'update' else ''),
                  data.get('provider') or '')
        art = article_for(cid)
        if art:
            db.exec_('''UPDATE articles SET section=?,town=?,focus=?,headline=?,entradilla=?,body=?,instagram_copy=?,headline_options_json=?,
                        slides_json=?,render_json=?,photo_query=?,sources_json=?,area_note=?,provider=?,status='draft',updated_at=CURRENT_TIMESTAMP
                        WHERE id=?''', fields + (art['id'],))
            aid = art['id']
        else:
            aid = db.exec_('''INSERT INTO articles(section,town,focus,headline,entradilla,body,instagram_copy,headline_options_json,slides_json,
                              render_json,photo_query,sources_json,area_note,provider,candidate_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', fields + (cid,))
        db.exec_("UPDATE candidates SET status='chosen',priority=CASE WHEN priority IN ('undecided','','no') THEN 'today' ELSE priority END WHERE id=?", (cid,))
        _work(cid, 'working', 'Buscando imágenes')
        try:
            gather_images(aid)
        except Exception as exc:
            db.log('images', 'Imágenes %s: %s' % (aid, exc))
        _work(cid, 'done', 'Listo')
        db.log('draft', 'Redactada: %s' % data['headline'])
        return db.row('SELECT * FROM articles WHERE id=?', (aid,))


def _add_media(aid, kind, url, source='', caption=''):
    if not url or db.row('SELECT 1 FROM media WHERE article_id=? AND url=? AND kind=?', (aid, url, kind)):
        return
    db.exec_('INSERT INTO media(article_id,kind,url,source,caption) VALUES(?,?,?,?,?)', (aid, kind, url, source, caption))


def gather_images(aid, query=None):
    """Foto de la fuente, fotos de otros medios y búsqueda en internet; los documentos del expediente van aparte (más lentos)."""
    art = db.row('SELECT * FROM articles WHERE id=?', (aid,))
    cand = db.row('SELECT * FROM candidates WHERE id=?', (art['candidate_id'],)) or {}
    if cand.get('image_hint'):
        _add_media(aid, 'fuente', photos.full_size(cand['image_hint']), cand.get('url'), 'Foto de la fuente')
    if (cand.get('url') or '').startswith('http'):
        for it in photos.page_images(photos.resolve_google_news(cand['url']), limit=4):
            _add_media(aid, 'fuente', it['url'], it['source'], 'Foto de la fuente')
    for l in sources.links_for([cand.get('id')]).get(cand.get('id'), [])[:3]:
        for it in photos.page_images(photos.resolve_google_news(l['url']), limit=2):
            _add_media(aid, 'medio', it['url'], it['source'], 'Foto de %s' % (l.get('outlet') or it['source_name']))
    q = (query or art.get('photo_query') or photos.photo_query_for(art.get('headline') or cand.get('title') or '')).strip()
    for it in photos.internet_images(q, limit=16):
        _add_media(aid, 'internet', it['url'], it['source'], it.get('source_name') or '')
    if cand.get('tag') in ('licitacion', 'edicto', 'obras', 'asi_sera') or cand.get('docs_json') not in (None, '', '[]'):
        threading.Thread(target=_docs_bg, args=(aid,), daemon=True).start()


def _docs_bg(aid):
    try:
        res = docs.extract_for_article(aid)
        db.set_setting('docs_%s' % aid, json.dumps(res, ensure_ascii=False))
    except Exception as exc:
        db.set_setting('docs_%s' % aid, json.dumps({'error': str(exc)[:300]}, ensure_ascii=False))


# ---------------------------------------------------------------- cola de redacción

def queue_write(cid, priority='today'):
    if cid in _queued:
        return False
    _queued.add(cid)
    _work(cid, 'queued', 'En cola')
    _queue.put((_PRIO.get(priority, 2), time.time(), cid))
    return True


def _worker():
    while True:
        _, _, cid = _queue.get()
        try:
            write_candidate(cid)
        except AIProviderError as exc:
            if exc.kind == 'rate_limit':
                time.sleep(min(600, exc.retry_after or 60))
        except Exception:
            pass
        finally:
            _queued.discard(cid)


def queue_pending():
    if not AUTO_DRAFT:
        return
    for c in db.rows("""SELECT c.id,c.priority FROM candidates c WHERE c.priority IN ('urgent','today','week','later')
                        AND c.status NOT IN ('archived','merged') AND (c.work_state IS NULL OR c.work_state='error')
                        AND NOT EXISTS (SELECT 1 FROM articles a WHERE a.candidate_id=c.id AND a.status!='rejected')"""):
        queue_write(c['id'], c['priority'])


# ---------------------------------------------------------------- propuesta del día

def best_today():
    """La mejor candidata para la pieza del día: exclusiva, reciente, con datos y que no esté ya en Área."""
    since = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    rows = db.rows("""SELECT * FROM candidates WHERE status='new' AND priority IN ('undecided','') AND area_state!='published'
                      AND block!='Nacionales adaptables' AND (published_at>=? OR tag='efemeride') ORDER BY score DESC LIMIT 40""", (since,))
    # Primero las licitaciones (exclusivas, con importe alto, de obras o servicios que cambian algo), después lo demás
    rows.sort(key=lambda r: (r['block'] == 'Licitaciones' and r.get('tstatus') != 'MENOR', r['exclusive'], r['score'],
                             r.get('amount_value') or 0, r['published_at'] or ''), reverse=True)
    return rows[0] if rows else None


def daily_pick(force=False):
    day = datetime.now(MADRID).date().isoformat()
    key = 'daily_pick_' + day
    current = db.setting(key)
    if current and not force:
        return db.row('SELECT * FROM candidates WHERE id=?', (int(current),))
    best = best_today()
    if not best:
        return None
    db.set_setting(key, str(best['id']))
    db.exec_("UPDATE candidates SET priority='today' WHERE id=?", (best['id'],))
    if AUTO_DRAFT:
        queue_write(best['id'], 'today')
    db.log('daily', 'Propuesta del día: %s' % best['title'])
    return best


def today_pick():
    cur = db.setting('daily_pick_' + datetime.now(MADRID).date().isoformat())
    return db.row('SELECT * FROM candidates WHERE id=?', (int(cur),)) if cur else None


# ---------------------------------------------------------------- programador

def _scheduler(interval_minutes):
    time.sleep(20)
    while True:
        try:
            sources.scan_all()
            if datetime.now(MADRID).hour >= DAILY_PICK_HOUR:
                daily_pick()
            queue_pending()
        except Exception as exc:
            db.log('error', 'Programador: %s' % exc)
        time.sleep(max(10, interval_minutes) * 60)


def start(interval_minutes):
    threading.Thread(target=_worker, daemon=True).start()
    threading.Thread(target=_scheduler, args=(interval_minutes,), daemon=True).start()
