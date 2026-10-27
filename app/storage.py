"""Espacio en disco: borra fotos e imágenes que ya no hacen falta para que no se llene el volumen.

Se conserva todo lo de las noticias sin publicar. De lo publicado, solo los últimos días (la web y las redes ya
tienen su copia). Las fotos descargadas que ninguna noticia usa se borran (se vuelven a bajar si hacen falta).
"""
import json
import shutil
import time
from pathlib import Path

from . import db
from .config import RENDER_DIR, UPLOAD_DIR

LOW = 300 * 1024 * 1024  # por debajo de 300 MB libres se limpia a fondo
_last = [0.0]


def free_bytes():
    for d in (UPLOAD_DIR, RENDER_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
            return shutil.disk_usage(d).free
        except OSError:
            continue
    return 0


def _paths_in(value):
    out = set()
    if not value:
        return out
    if isinstance(value, str) and value.strip().startswith(('[', '{')):
        try:
            value = json.loads(value)
        except ValueError:
            return out
    if isinstance(value, str):
        if '/' in value:
            out.add(value)
    elif isinstance(value, dict):
        for v in value.values():
            out |= _paths_in(v)
    elif isinstance(value, list):
        for v in value:
            out |= _paths_in(v)
    return out


def _keep(published_days):
    """Archivos que siguen haciendo falta: los de noticias sin publicar y los de lo publicado hace poco."""
    keep, ids = set(), set()
    rows = db.rows("""SELECT id,image_local,render_path,image_candidates_json,carousel_json,status,updated_at FROM articles
                      WHERE status NOT IN ('published','rejected')
                         OR (status='published' AND updated_at>=datetime('now',?))""", ('-%d day' % published_days,))
    for r in rows:
        ids.add(r['id'])
        for k in ('image_local', 'render_path', 'image_candidates_json', 'carousel_json'):
            keep |= {str(Path(p).name) for p in _paths_in(r.get(k))}
    return keep, ids


def cleanup(deep=False):
    """Borra lo que sobra. deep=True cuando queda poco espacio. Devuelve los MB liberados."""
    now = time.time()
    keep, ids = _keep(1 if deep else 4)
    young = 10 * 60 if deep else 2 * 86400  # una foto recién bajada puede estar a punto de usarse
    freed = 0

    def drop(f):
        nonlocal freed
        try:
            size = f.stat().st_size
            f.unlink()
            freed += size
        except OSError:
            pass

    if UPLOAD_DIR.is_dir():
        for f in UPLOAD_DIR.iterdir():
            try:
                if f.is_file() and f.name not in keep and now - f.stat().st_mtime > young:
                    drop(f)
            except OSError:
                continue
    if RENDER_DIR.is_dir():
        for f in RENDER_DIR.iterdir():
            try:
                if not f.is_file():
                    continue
                age = now - f.stat().st_mtime
                name = f.name
                if name.startswith('public_'):
                    if age > (2 if deep else 8) * 86400:
                        drop(f)
                elif name.endswith('.zip'):
                    if age > (600 if deep else 86400):
                        drop(f)
                elif name.startswith('article_'):
                    try:
                        aid = int(name.split('_')[1].split('.')[0])
                    except (IndexError, ValueError):
                        aid = None
                    if aid not in ids and name not in keep and age > young:
                        drop(f)
                elif name not in keep and age > (young if deep else 7 * 86400):
                    drop(f)
            except OSError:
                continue
    try:  # textos de fuentes de tarjetas retiradas hace tiempo: ocupan mucho en la base de datos
        db.exec_("UPDATE candidates SET raw_text=NULL WHERE status IN ('archived','merged') AND raw_text IS NOT NULL "
                 "AND created_at<datetime('now','-30 day')")
        db.exec_("DELETE FROM activity WHERE created_at<datetime('now','-60 day')")
    except Exception:
        pass
    _last[0] = now
    if freed:
        try:
            db.log('storage', 'Limpieza de disco: %d MB liberados' % (freed // (1024 * 1024)))
        except Exception:
            pass
    return freed // (1024 * 1024)


def ensure_space():
    """Antes de guardar una imagen: si queda poco sitio, limpia a fondo (como mucho una vez por minuto)."""
    if free_bytes() < LOW and time.time() - _last[0] > 60:
        cleanup(deep=True)
