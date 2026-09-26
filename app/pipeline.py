import json
from . import db, sources, ai, photos, renderer
from .config import QUICK_SCORE_MAX, MIN_AUTO_SCORE

def process_candidate(cid,force_research=False):
    c=db.row('SELECT * FROM candidates WHERE id=?',(cid,))
    if not c: raise RuntimeError('Candidata no existe')
    quick=c['score']<=QUICK_SCORE_MAX and not force_research
    src_text=sources.fetch_article_text(c.get('url','')); research_data={}
    if not quick:
        try: research_data=ai.research(c,src_text)
        except Exception as e: research_data={'facts':[],'sources':[],'caveats':[str(e)]}
    elif not ai.OPENAI_API_KEY:
        research_data=ai.research(c,src_text)
    draft=ai.draft(c,src_text,json.dumps(research_data,ensure_ascii=False),quick=quick)
    image_candidates=photos.search_real_photos(c,draft.get('section','CIUDAD'))
    chosen=image_candidates[0] if image_candidates else None; local=''
    if chosen:
        try: local=photos.download_image(chosen['url'])
        except Exception: local=''
    active_template=db.row('SELECT id FROM templates WHERE active=1 ORDER BY id DESC LIMIT 1')
    aid=db.exec_('''INSERT OR REPLACE INTO articles(candidate_id,section,headline,subtitle,body,social_text,graphic_summary,research_notes,sources_json,image_url,image_source,image_license,image_local,image_candidates_json,ai_image_suggestion,template_id,workflow,status)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
        cid,draft.get('section','CIUDAD'),draft.get('headline',''),draft.get('subtitle',''),draft.get('body','')[:2200],draft.get('body','')[:2200],draft.get('graphic_summary','')[:240],
        json.dumps(research_data,ensure_ascii=False),json.dumps((research_data or {}).get('sources',[]),ensure_ascii=False),
        chosen.get('url','') if chosen else '',chosen.get('source','') if chosen else '',chosen.get('license','') if chosen else '',local,json.dumps(image_candidates,ensure_ascii=False),
        draft.get('ai_image_suggestion','') if not chosen else '',active_template['id'] if active_template else 1,'quick' if quick or not ai.OPENAI_API_KEY else 'researched','review_ready'))
    db.exec_('UPDATE candidates SET status=?,section=? WHERE id=?',('review_ready',draft.get('section','CIUDAD'),cid))
    try: renderer.render_article(aid)
    except Exception as e: db.log('render_error',f'{aid}: {e}')
    db.log('pipeline',f'Candidata {cid} -> revisión ({"rápida" if quick else "investigada"})')
    return aid

def auto_process(ids):
    done=[]; errors=[]
    for cid in ids:
        c=db.row('SELECT * FROM candidates WHERE id=?',(cid,))
        if not c: continue
        if c['score']<MIN_AUTO_SCORE:
            db.exec_('UPDATE candidates SET status=? WHERE id=?',('archived',cid)); continue
        try: done.append(process_candidate(cid))
        except Exception as e:
            errors.append({'id':cid,'error':str(e)}); db.exec_('UPDATE candidates SET status=? WHERE id=?',('needs_config',cid))
    return {'done':done,'errors':errors}
