import json, threading
from . import db, sources, ai, photos
from .config import QUICK_SCORE_MAX, MIN_AUTO_SCORE, AUTO_DRAFTS_PER_SCAN, AUTO_DRAFT_USEFUL

# One lock per candidate so two clicks on «Redactar» never create two drafts.
_locks = {}
_locks_guard = threading.Lock()


def _lock_for(cid):
    with _locks_guard:
        return _locks.setdefault(int(cid), threading.Lock())


def _set_work(cid, state, step=None, error=None):
    db.exec_("UPDATE candidates SET work_state=?,work_step=?,work_error=?,work_updated_at=CURRENT_TIMESTAMP WHERE id=?",
             (state, step, error, cid))


def existing_article(cid):
    """The live article for a candidate (any status except rejected)."""
    return db.row("SELECT * FROM articles WHERE candidate_id=? AND status!='rejected'", (cid,))


def investigate_candidate(cid):
    c = db.row('SELECT * FROM candidates WHERE id=?', (cid,))
    if not c: raise RuntimeError('Candidata no existe')
    if c['status'] not in ('new', 'needs_config'): raise ValueError('La noticia ya ha avanzado desde el radar')
    src_text = sources.fetch_article_text(c.get('url', ''))
    if len(src_text or '') < 300 and len(c.get('excerpt') or '') > len(src_text or ''):
        src_text = c.get('excerpt') or src_text  # si la página no se deja leer, al menos el resumen completo de la fuente
    extra = sources.links_for([cid]).get(cid, [])[:3]  # la misma noticia en otras fuentes: se combinan
    for link in extra:
        other = sources.fetch_article_text(link['url']) or link.get('title') or ''
        src_text = (src_text or '') + '\n\n--- OTRA FUENTE: %s (%s) ---\n%s' % (link.get('outlet') or link.get('source_name') or '', link['url'], other[:6000])
    try:
        research_data = ai.research(c, src_text)
    except ai.AIProviderError as e:
        # Keep going with the original source; the editor sees why the contrast is missing.
        research_data = ai._source_research(c, src_text)
        research_data['caveats'].append('No se pudo completar la investigación con IA: ' + str(e)[:250])
        research_data['ai_error'] = e.as_dict()
    known = {x.get('url') for x in research_data.get('sources') or []}
    research_data.setdefault('sources', []).extend({'name': l.get('outlet') or l.get('source_name') or 'Otra fuente', 'url': l['url']}
                                                   for l in extra if l['url'] not in known)
    db.exec_("UPDATE candidates SET status='researched',raw_text=?,research_json=? WHERE id=?",
             (src_text, json.dumps(research_data, ensure_ascii=False), cid))
    db.log('investigation', f'Candidata {cid} investigada; pendiente de redacción')
    return research_data


def draft_candidate(cid):
    c = db.row('SELECT * FROM candidates WHERE id=?', (cid,))
    if not c: raise RuntimeError('Candidata no existe')
    current = existing_article(cid)
    if current:
        return current['id']  # Never overwrite or duplicate an existing draft.
    if c['status'] != 'researched': raise ValueError('Primero investiga la noticia')
    try: research_data = json.loads(c.get('research_json') or '{}')
    except json.JSONDecodeError: research_data = {}
    quick = (c['score'] or 0) <= QUICK_SCORE_MAX
    draft = ai.draft(c, c.get('raw_text') or '', json.dumps(research_data, ensure_ascii=False), quick=quick)
    try:
        image_candidates = photos.search_real_photos(c, draft.get('section', 'CIUDAD'), draft.get('photo_query'))
    except Exception:
        image_candidates = []
    # Se elige la primera foto válida (normalmente la de la propia noticia); el editor puede cambiarla.
    chosen, local = photos.first_usable(image_candidates)
    body = (draft.get('body') or '')[:2200]
    workflow = 'source_draft' if not ai.AI_ENABLED else ('quick' if quick else 'researched')
    old = db.row("SELECT id FROM articles WHERE candidate_id=? AND status='rejected'", (cid,))
    if old:  # A previously discarded draft is replaced by the new one (candidate_id is unique).
        db.exec_('DELETE FROM articles WHERE id=?', (old['id'],))
    aid = db.exec_('''INSERT INTO articles(candidate_id,section,headline,subtitle,body,social_text,graphic_summary,research_notes,sources_json,
        image_url,image_source,image_license,image_local,image_kind,image_author,image_candidates_json,ai_image_suggestion,
        carousel_suitable,carousel_reason,headline_options_json,missing_data_json,ai_provider,template_id,workflow,status,image_headline,focus,photo_query)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
        cid, draft.get('section', 'CIUDAD'), draft.get('headline', ''), draft.get('subtitle', ''), body, body,
        (draft.get('graphic_summary') or '')[:240],
        json.dumps(research_data, ensure_ascii=False), json.dumps((research_data or {}).get('sources', []), ensure_ascii=False),
        chosen.get('url', '') if chosen else '', chosen.get('source', '') if chosen else '',
        chosen.get('license', '') if chosen else '', local, chosen.get('kind', '') if chosen else '',
        chosen.get('author', '') if chosen else '', json.dumps(image_candidates, ensure_ascii=False),
        draft.get('ai_image_suggestion', '') or '', int(bool(draft.get('carousel_suitable'))),
        str(draft.get('carousel_reason') or '')[:500],
        json.dumps(draft.get('headline_options') or [], ensure_ascii=False),
        json.dumps(draft.get('missing_data') or [], ensure_ascii=False),
        draft.get('provider') or '', None, workflow, 'draft', draft.get('image_headline') or '', str(draft.get('focus') or '')[:300], str(draft.get('photo_query') or '')[:120]))
    db.exec_('UPDATE articles SET brand=? WHERE id=?', (c.get('brand') or 'infolinense', aid))
    db.exec_('UPDATE candidates SET status=?,section=? WHERE id=?', ('draft', draft.get('section', 'CIUDAD'), cid))
    db.log('pipeline', f'Candidata {cid} -> borrador')
    return aid


def write_candidate(cid):
    """The «Redactar» button: open an existing draft, or research (if needed) and draft.

    Returns {'action': 'opened'|'drafted', 'article_id': id}. Raises ai.AIProviderError,
    ValueError or RuntimeError; a concurrent call gets ValueError('busy').
    """
    lock = _lock_for(cid)
    if not lock.acquire(blocking=False):
        raise ValueError('busy')
    try:
        c = db.row('SELECT * FROM candidates WHERE id=?', (cid,))
        if not c: raise RuntimeError('Noticia no encontrada')
        current = existing_article(cid)
        if current:
            _set_work(cid, 'done')
            return {'action': 'opened', 'article_id': current['id']}
        if c['status'] == 'archived':
            db.exec_("UPDATE candidates SET status=CASE WHEN research_json IS NULL THEN 'new' ELSE 'researched' END WHERE id=?", (cid,))
            c = db.row('SELECT * FROM candidates WHERE id=?', (cid,))
        if c['status'] in ('draft', 'review_ready'):
            # Status says there is a draft but it was deleted/rejected: rebuild from research.
            db.exec_("UPDATE candidates SET status=CASE WHEN research_json IS NULL THEN 'new' ELSE 'researched' END WHERE id=?", (cid,))
            c = db.row('SELECT * FROM candidates WHERE id=?', (cid,))
        if c['status'] == 'researched' and '"ai_error"' in (c.get('research_json') or ''):
            # The earlier investigation fell back to the bare source because the AI failed: redo it.
            db.exec_("UPDATE candidates SET status='new' WHERE id=?", (cid,))
            c = db.row('SELECT * FROM candidates WHERE id=?', (cid,))
        if c['status'] in ('new', 'needs_config'):
            _set_work(cid, 'working', 'investigating')
            investigate_candidate(cid)
        _set_work(cid, 'working', 'drafting')
        aid = draft_candidate(cid)
        _set_work(cid, 'done')
        return {'action': 'drafted', 'article_id': aid}
    except ai.AIProviderError as e:
        _set_work(cid, 'error', None, json.dumps(e.as_dict(), ensure_ascii=False))
        raise
    except Exception as e:
        _set_work(cid, 'error', None, json.dumps({'message': str(e)[:300], 'kind': 'error', 'retryable': True}, ensure_ascii=False))
        raise
    finally:
        lock.release()


# ---------- Redacción automática de las noticias marcadas como útiles ----------
import queue as _queue
from datetime import datetime as _dt
_auto_q = _queue.PriorityQueue()
_auto_pending = set()
_auto_guard = threading.Lock()
_auto_thread = None
_paused_until = [0.0]
_PRIO = {'urgent': 0, 'today': 1, 'this_week': 2, 'future': 3}
USEFUL = tuple(_PRIO)


def queue_auto_write(cid, priority=None):
    """Pone en cola la redacción de una noticia útil (una a una, sin duplicados)."""
    global _auto_thread
    if not AUTO_DRAFT_USEFUL:
        return False
    c = db.row('SELECT id,editorial_priority,status FROM candidates WHERE id=?', (cid,))
    if not c or (priority or c['editorial_priority']) not in _PRIO or existing_article(cid) or c['status'] == 'archived':
        return False
    with _auto_guard:
        if cid in _auto_pending or is_working(cid):
            return False
        if _dt.now().timestamp() < _paused_until[0]:
            return False  # cupo diario gratuito agotado: se reintentará en la siguiente búsqueda
        _auto_pending.add(cid)
        _set_work(cid, 'queued', 'queued')
        _auto_q.put((_PRIO[priority or c['editorial_priority']], _dt.now().timestamp(), cid))
        if _auto_thread is None or not _auto_thread.is_alive():
            _auto_thread = threading.Thread(target=_auto_worker, daemon=True)
            _auto_thread.start()
    return True


def _auto_worker():
    while True:
        try:
            _, _, cid = _auto_q.get(timeout=30)
        except _queue.Empty:
            return
        try:
            c = db.row('SELECT editorial_priority,status FROM candidates WHERE id=?', (cid,))
            if c and c['editorial_priority'] in _PRIO and c['status'] != 'archived' and not existing_article(cid):
                try:
                    write_candidate(cid)
                    db.log('auto_draft', f'Candidata {cid} redactada automáticamente')
                except ai.AIProviderError as e:
                    if e.kind == 'rate_limit' and e.code != 'daily':
                        # Límite por minuto del plan gratis: vuelve a la cola dentro de un rato, sin marcar error.
                        _set_work(cid, 'queued', 'queued')
                        threading.Timer(90, lambda c=cid: queue_auto_write(c)).start()
                    elif e.kind == 'rate_limit':
                        _paused_until[0] = _dt.now().timestamp() + 3600  # cupo diario agotado: se deja de intentar una hora
                    db.log('auto_draft_error', f'{cid}: {str(e)[:200]}')
                except ValueError as e:
                    if str(e) != 'busy':
                        db.log('auto_draft_error', f'{cid}: {str(e)[:200]}')
                except Exception as e:
                    db.log('auto_draft_error', f'{cid}: {str(e)[:200]}')
            elif c and c.get('status') != 'archived':
                db.exec_("UPDATE candidates SET work_state=NULL WHERE id=? AND work_state='queued'", (cid,))
        finally:
            with _auto_guard:
                _auto_pending.discard(cid)


def queue_useful_pending(retry_errors=True):
    """Encola todas las noticias útiles sin borrador (al arrancar y tras cada búsqueda)."""
    rows = db.rows("""SELECT c.id,c.editorial_priority,c.work_state FROM candidates c
                     LEFT JOIN articles a ON a.candidate_id=c.id AND a.status!='rejected'
                     WHERE a.id IS NULL AND c.status NOT IN ('archived') AND c.editorial_priority IN ('urgent','today','this_week','future')""")
    n = 0
    for r in rows:
        if r.get('work_state') == 'error' and not retry_errors:
            continue
        n += bool(queue_auto_write(r['id'], r['editorial_priority']))
    return n


def is_working(cid):
    lock = _lock_for(cid)
    if lock.acquire(blocking=False):
        lock.release()
        return False
    return True


def submit_candidate(cid):
    c = db.row('SELECT status FROM candidates WHERE id=?', (cid,))
    a = db.row("SELECT id,status FROM articles WHERE candidate_id=? AND status!='rejected'", (cid,))
    if not c or not a: raise ValueError('Primero redacta y guarda un borrador')
    if a['status'] in ('review_ready', 'approved', 'published'):
        return a['id']
    db.exec_("UPDATE articles SET status='review_ready',updated_at=CURRENT_TIMESTAMP WHERE id=?", (a['id'],))
    db.exec_("UPDATE candidates SET status='review_ready' WHERE id=?", (cid,))
    db.log('pipeline', f'Candidata {cid} -> revisión')
    return a['id']


def process_candidate(cid, force_research=False):
    """Compatibility path for older clients; the UI uses explicit steps."""
    c = db.row('SELECT status FROM candidates WHERE id=?', (cid,))
    if not c: raise RuntimeError('Candidata no existe')
    if c['status'] in ('new', 'needs_config'): investigate_candidate(cid)
    if db.row('SELECT status FROM candidates WHERE id=?', (cid,))['status'] == 'researched':
        draft_candidate(cid)
    return submit_candidate(cid)


def auto_process(ids):
    done = []; errors = []
    for cid in ids:
        c = db.row('SELECT id,score FROM candidates WHERE id=? AND status=?', (cid, 'new'))
        if c and c['score'] < MIN_AUTO_SCORE:
            db.exec_('UPDATE candidates SET status=? WHERE id=?', ('archived', cid))
    queued = db.rows("SELECT id FROM candidates WHERE status='new' AND score>=? ORDER BY score DESC,id DESC LIMIT ?", (MIN_AUTO_SCORE, max(0, AUTO_DRAFTS_PER_SCAN)))
    for c in queued:
        cid = c['id']
        try: done.append(process_candidate(cid))
        except Exception as e:
            errors.append({'id': cid, 'error': str(e)}); db.exec_('UPDATE candidates SET status=? WHERE id=?', ('needs_config', cid))
    return {'done': done, 'errors': errors}
