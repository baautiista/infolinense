"""Manual Cofrade searches use the same scan status and normal editorial queue."""
import threading
import time
from . import db

_lock = threading.Lock()
state = {'busy': False, 'started_at': 0, 'finished_at': 0, 'added': 0, 'errors': [], 'phase': '', 'checked': 0, 'total': 0}


def start(other_sources=None):
    if not _lock.acquire(blocking=False):
        return dict(state, started=False)
    state.update(busy=True, started_at=time.time(), finished_at=0, added=0, errors=[], phase='Facebook: preparando búsqueda', checked=0, total=0)
    threading.Thread(target=_run, args=(other_sources,), daemon=True).start()
    return dict(state, started=True)


def _run(other_sources):
    from . import facebook_reader, facebook_apify
    try:
        sources = db.rows("SELECT url FROM sources WHERE brand='cofrade' AND kind='social' AND active=1 AND official=1")
        urls = [s['url'] for s in sources if facebook_reader.account(s['url'])]
        state['total'] = len(urls)
        if facebook_reader.configuration()['provider'] == 'apify':
            run_id = facebook_apify.run_task(urls, lambda message: state.update(phase=message))
        else:
            run_id = None
        state['phase'] = 'Facebook: importando publicaciones a Ordenar'
        result = facebook_reader.import_recent(run_id=run_id)
        state.update(added=len(result['added']), checked=len(urls), errors=result['errors'])
        if run_id:
            db.set_setting('facebook_apify_run', '{}')
        if other_sources:
            state['phase'] = 'Consultando las demás fuentes'
            extra = other_sources()
            state['added'] += len(extra.get('added') or [])
            state['errors'].extend(extra.get('errors') or [])
    except Exception as exc:
        from .social_publish import SocialError
        state['errors'] = [str(exc) if isinstance(exc, SocialError) else 'No se pudo completar la búsqueda de Facebook. Revisa el estado de la tarea en Apify.']
        db.exec_("UPDATE sources SET last_checked_at=CURRENT_TIMESTAMP,last_error=? WHERE brand='cofrade' AND kind='social' AND official=1 AND active=1", (state['errors'][0],))
    finally:
        state.update(busy=False, phase='', finished_at=time.time())
        _lock.release()
