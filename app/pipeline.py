import json
from . import db, sources, ai, photos
from .config import QUICK_SCORE_MAX, MIN_AUTO_SCORE, AUTO_DRAFTS_PER_SCAN

def investigate_candidate(cid):
    c=db.row('SELECT * FROM candidates WHERE id=?',(cid,))
    if not c: raise RuntimeError('Candidata no existe')
    if c['status']!='new': raise ValueError('La noticia ya ha avanzado desde el radar')
    src_text=sources.fetch_article_text(c.get('url',''))
    try: research_data=ai.research(c,src_text)
    except Exception as e:
        research_data={'facts':[],'sources':[{'name':c.get('source_name'),'url':c.get('url')}],
                       'caveats':['No se pudo completar el contraste: '+str(e)[:250]]}
    db.exec_("UPDATE candidates SET status='researched',raw_text=?,research_json=? WHERE id=?",
             (src_text,json.dumps(research_data,ensure_ascii=False),cid))
    db.log('investigation',f'Candidata {cid} investigada; pendiente de redacción')
    return research_data

def draft_candidate(cid):
    c=db.row('SELECT * FROM candidates WHERE id=?',(cid,))
    if not c: raise RuntimeError('Candidata no existe')
    if c['status']!='researched': raise ValueError('Primero investiga la noticia')
    try: research_data=json.loads(c.get('research_json') or '{}')
    except json.JSONDecodeError: research_data={}
    quick=c['score']<=QUICK_SCORE_MAX
    draft=ai.draft(c,c.get('raw_text') or '',json.dumps(research_data,ensure_ascii=False),quick=quick)
    image_candidates=photos.search_real_photos(c,draft.get('section','CIUDAD'))
    chosen=image_candidates[0] if image_candidates else None; local=''
    if chosen:
        try: local=photos.download_image(chosen['url'])
        except Exception: local=''
    aid=db.exec_('''INSERT OR REPLACE INTO articles(candidate_id,section,headline,subtitle,body,social_text,graphic_summary,research_notes,sources_json,image_url,image_source,image_license,image_local,image_candidates_json,ai_image_suggestion,template_id,workflow,status)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
        cid,draft.get('section','CIUDAD'),draft.get('headline',''),draft.get('subtitle',''),draft.get('body','')[:2200],draft.get('body','')[:2200],draft.get('graphic_summary','')[:240],
        json.dumps(research_data,ensure_ascii=False),json.dumps((research_data or {}).get('sources',[]),ensure_ascii=False),
        chosen.get('url','') if chosen else '',chosen.get('source','') if chosen else '',chosen.get('license','') if chosen else '',local,json.dumps(image_candidates,ensure_ascii=False),
        draft.get('ai_image_suggestion','') if not chosen else '',None,'source_draft' if not ai.OPENAI_API_KEY else ('quick' if quick else 'researched'),'draft'))
    db.exec_('UPDATE candidates SET status=?,section=? WHERE id=?',('draft',draft.get('section','CIUDAD'),cid))
    db.log('pipeline',f'Candidata {cid} -> borrador')
    return aid

def submit_candidate(cid):
    c=db.row('SELECT status FROM candidates WHERE id=?',(cid,))
    a=db.row('SELECT id,status FROM articles WHERE candidate_id=?',(cid,))
    if not c or not a or c['status']!='draft' or a['status']!='draft':
        raise ValueError('Primero redacta y guarda un borrador')
    db.exec_("UPDATE articles SET status='review_ready',updated_at=CURRENT_TIMESTAMP WHERE id=?",(a['id'],))
    db.exec_("UPDATE candidates SET status='review_ready' WHERE id=?",(cid,))
    db.log('pipeline',f'Candidata {cid} -> revisión')
    return a['id']

def process_candidate(cid,force_research=False):
    """Compatibility path for older clients; the UI uses explicit steps."""
    c=db.row('SELECT status FROM candidates WHERE id=?',(cid,))
    if not c: raise RuntimeError('Candidata no existe')
    if c['status']=='new': investigate_candidate(cid)
    if db.row('SELECT status FROM candidates WHERE id=?',(cid,))['status']=='researched':
        draft_candidate(cid)
    return submit_candidate(cid)

def auto_process(ids):
    done=[]; errors=[]
    for cid in ids:
        c=db.row('SELECT id,score FROM candidates WHERE id=? AND status=?',(cid,'new'))
        if c and c['score']<MIN_AUTO_SCORE:
            db.exec_('UPDATE candidates SET status=? WHERE id=?',('archived',cid))
    queued=db.rows("SELECT id FROM candidates WHERE status='new' AND score>=? ORDER BY score DESC,id DESC LIMIT ?",(MIN_AUTO_SCORE,max(0,AUTO_DRAFTS_PER_SCAN)))
    for c in queued:
        cid=c['id']
        try: done.append(process_candidate(cid))
        except Exception as e:
            errors.append({'id':cid,'error':str(e)}); db.exec_('UPDATE candidates SET status=? WHERE id=?',('needs_config',cid))
    return {'done':done,'errors':errors}
