import json, threading
from . import db, sources, ai, photos
from .config import QUICK_SCORE_MAX, MIN_AUTO_SCORE, AUTO_DRAFTS_PER_SCAN

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
    try:
        research_data = ai.research(c, src_text)
    except ai.AIProviderError as e:
        # Keep going with the original source; the editor sees why the contrast is missing.
        research_data = ai._source_research(c, src_text)
        research_data['caveats'].append('No se pudo completar la investigación con IA: ' + str(e)[:250])
        research_data['ai_error'] = e.as_dict()
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
    image_candidates = photos.search_real_photos(c, draft.get('section', 'CIUDAD'))
    # Preselect only a photo whose reuse is documented; the editor can choose another one.
    chosen = next((p for p in image_candidates if p.get('publish_safe')), None)
    local = ''
    if chosen:
        try: local = photos.download_image(chosen['url'])
        except Exception: chosen = None; local = ''
    body = (draft.get('body') or '')[:2200]
    workflow = 'source_draft' if not ai.AI_ENABLED else ('quick' if quick else 'researched')
    old = db.row("SELECT id FROM articles WHERE candidate_id=? AND status='rejected'", (cid,))
    if old:  # A previously discarded draft is replaced by the new one (candidate_id is unique).
        db.exec_('DELETE FROM articles WHERE id=?', (old['id'],))
    aid = db.exec_('''INSERT INTO articles(candidate_id,section,headline,subtitle,body,social_text,graphic_summary,research_notes,sources_json,
        image_url,image_source,image_license,image_local,image_kind,image_author,image_candidates_json,ai_image_suggestion,
        carousel_suitable,carousel_reason,headline_options_json,missing_data_json,ai_provider,template_id,workflow,status)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
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
        draft.get('provider') or '', None, workflow, 'draft'))
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
