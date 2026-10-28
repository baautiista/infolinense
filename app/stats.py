"""Estadísticas de cada noticia en Instagram y Facebook (me gusta, comentarios, compartidos y, si Meta lo permite,
alcance) y «franjas inteligentes»: qué hora funciona mejor para cada sección, aprendido de lo publicado."""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import db

TZ = ZoneInfo('Europe/Madrid')
GRID = ['08:30', '10:00', '11:30', '13:00', '16:00', '17:45', '19:30', '21:00', '22:30']


def _ensure():
    db.exec_('''CREATE TABLE IF NOT EXISTS post_stats(article_id INTEGER NOT NULL, network TEXT NOT NULL, likes INTEGER DEFAULT 0,
                comments INTEGER DEFAULT 0, shares INTEGER DEFAULT 0, reach INTEGER DEFAULT 0, saved INTEGER DEFAULT 0,
                score REAL DEFAULT 0, fetched_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(article_id,network))''')


def _score(likes, comments, shares, reach, saved):
    return likes + 2 * comments + 3 * shares + 2 * saved + reach / 100.0


def _fb(post_id, token):
    from .social_publish import _graph
    d = _graph('GET', '/%s' % post_id, params={'fields': 'reactions.summary(true).limit(0),comments.summary(true).limit(0),shares',
                                               'access_token': token})
    likes = ((d.get('reactions') or {}).get('summary') or {}).get('total_count', 0)
    comments = ((d.get('comments') or {}).get('summary') or {}).get('total_count', 0)
    shares = (d.get('shares') or {}).get('count', 0)
    reach = 0
    try:  # necesita read_insights; si no está, se queda sin alcance
        ins = _graph('GET', '/%s/insights' % post_id, params={'metric': 'post_impressions_unique', 'access_token': token})
        reach = int(((ins.get('data') or [{}])[0].get('values') or [{}])[0].get('value') or 0)
    except Exception:
        pass
    return likes, comments, shares, reach, 0


def _ig(media_id, token, base):
    from .social_publish import _graph
    d = _graph('GET', '/%s' % media_id, base=base, params={'fields': 'like_count,comments_count', 'access_token': token})
    likes, comments, reach, saved, shares = d.get('like_count', 0) or 0, d.get('comments_count', 0) or 0, 0, 0, 0
    try:
        ins = _graph('GET', '/%s/insights' % media_id, base=base, params={'metric': 'reach,saved,shares', 'access_token': token})
        vals = {m.get('name'): ((m.get('values') or [{}])[0].get('value') or (m.get('total_value') or {}).get('value') or 0)
                for m in ins.get('data') or []}
        reach, saved, shares = int(vals.get('reach') or 0), int(vals.get('saved') or 0), int(vals.get('shares') or 0)
    except Exception:
        pass
    return likes, comments, shares, reach, saved


def refresh(days=14):
    """Lee las cifras de lo publicado en los últimos días. Devuelve cuántas publicaciones se han actualizado."""
    from . import social_publish as sp
    _ensure()
    n = 0
    rows = db.rows('''SELECT p.article_id,p.network,p.remote_id,a.brand FROM social_posts p JOIN articles a ON a.id=p.article_id
                      WHERE p.status='published' AND p.network IN ('facebook','instagram') AND COALESCE(p.remote_id,'')!=''
                        AND p.updated_at>=datetime('now',?)''', ('-%d day' % days,))
    for r in rows:
        try:
            with sp.use_brand(r.get('brand') or 'infolinense'):
                if r['network'] == 'facebook':
                    page = sp._meta()
                    if not page.get('access_token'):
                        continue
                    vals = _fb(r['remote_id'], page['access_token'])
                else:
                    direct = sp._instagram_direct()
                    if direct:
                        token, base = direct['access_token'], sp.IG_GRAPH
                    else:
                        page = sp._meta()
                        if not page.get('access_token'):
                            continue
                        token, base = page['access_token'], None
                    vals = _ig(r['remote_id'], token, base)
        except Exception as exc:
            db.log('stats_error', ('%s %s: %s' % (r['network'], r['article_id'], exc))[:250])
            continue
        db.exec_('''INSERT OR REPLACE INTO post_stats(article_id,network,likes,comments,shares,reach,saved,score,fetched_at)
                    VALUES(?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)''', (r['article_id'], r['network'], *vals, _score(*vals)))
        n += 1
    db.set_setting('stats_refreshed_at', datetime.now(timezone.utc).isoformat(timespec='seconds'))
    return n


def _published_slot(updated_at):
    try:
        d = datetime.fromisoformat(str(updated_at).replace(' ', 'T')).replace(tzinfo=timezone.utc).astimezone(TZ)
    except ValueError:
        return '', ''
    hhmm = d.strftime('%H:%M')
    m = int(hhmm[:2]) * 60 + int(hhmm[3:])
    slot = min(GRID, key=lambda g: abs(int(g[:2]) * 60 + int(g[3:]) - m))
    return d.date().isoformat(), slot


def _family(section, headline=''):
    from . import layout
    try:
        return layout.family_for(section or '', headline or '')[0]
    except Exception:
        return (section or '').upper()


def listing(days=30, brand=''):
    _ensure()
    rows = db.rows('''SELECT a.id,a.brand,a.headline,a.section,a.updated_at,a.publish_url,
                      SUM(s.likes) likes,SUM(s.comments) comments,SUM(s.shares) shares,SUM(s.reach) reach,SUM(s.saved) saved,SUM(s.score) score,
                      GROUP_CONCAT(s.network) networks
                      FROM articles a JOIN post_stats s ON s.article_id=a.id
                      WHERE a.status='published' AND a.updated_at>=datetime('now',?) GROUP BY a.id ORDER BY score DESC''', ('-%d day' % days,))
    if brand and brand != 'all':
        rows = [r for r in rows if (r.get('brand') or 'infolinense') == brand]
    for r in rows:
        r['day'], r['slot'] = _published_slot(r['updated_at'])
        r['family'] = _family(r.get('section'), r.get('headline'))
    return rows


def best_slots(days=60, brand=''):
    """Puntuación media por franja, en general y por sección. Solo cuenta con 2 o más publicaciones en esa franja."""
    rows = listing(days, brand)
    if not rows:
        return {'overall': {}, 'by_section': {}, 'samples': 0}
    avg_all = sum(r['score'] or 0 for r in rows) / len(rows) or 1
    by_slot, by_sec = {}, {}
    for r in rows:
        rel = (r['score'] or 0) / avg_all  # 1 = lo normal; 2 = el doble de lo normal
        by_slot.setdefault(r['slot'], []).append(rel)
        by_sec.setdefault(r['family'], {}).setdefault(r['slot'], []).append(rel)
    mean = lambda xs: round(sum(xs) / len(xs), 2)
    overall = {s: mean(v) for s, v in by_slot.items() if len(v) >= 2}
    sections = {sec: {s: mean(v) for s, v in slots.items() if len(v) >= 2} for sec, slots in by_sec.items()}
    return {'overall': overall, 'by_section': {k: v for k, v in sections.items() if v}, 'samples': len(rows)}


def predict(section, headline, slot, table=None):
    """Lo bien que suele ir esa sección a esa hora (1 = normal). Sin datos, 1."""
    table = table or best_slots()
    fam = _family(section, headline)
    return table['by_section'].get(fam, {}).get(slot) or table['overall'].get(slot) or 1.0
