"""Vídeos de las noticias: subida validada (formato, tamaño, duración, medidas y orientación), portada, vista previa
y comprobación de lo que admite cada red. Los archivos se guardan en el volumen y se borran 48 h después de publicarse.

Límites que se comprueban (los más estrictos de cada red, para no fallar al publicar):
- Instagram Reels: MP4/MOV, de 3 s a 15 min, hasta 300 MB; mejor vertical 9:16.
- Facebook Reels: de 3 a 90 s, vertical 9:16, al menos 540×960. Si no cumple, va como vídeo normal de la página.
- TikTok: hasta 10 min por la API.
"""
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from . import db
from .config import UPLOAD_DIR

MAX_BYTES = 300 * 1024 * 1024
EXTS = {'.mp4': 'video/mp4', '.mov': 'video/quicktime', '.m4v': 'video/mp4'}
VIDEO_DIR = UPLOAD_DIR / 'videos'


def _ensure():
    db.exec_('''CREATE TABLE IF NOT EXISTS videos(id INTEGER PRIMARY KEY AUTOINCREMENT, article_id INTEGER NOT NULL, position INTEGER NOT NULL DEFAULT 0,
                path TEXT NOT NULL, filename TEXT, size INTEGER, duration REAL, width INTEGER, height INTEGER, orientation TEXT, codec TEXT,
                thumb_path TEXT, cover_second REAL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')


def available():
    return bool(shutil.which('ffprobe') and shutil.which('ffmpeg'))


def probe(path):
    """Duración, medidas, códec y orientación con ffprobe."""
    out = subprocess.run(['ffprobe', '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', str(path)],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise ValueError('El archivo no es un vídeo válido')
    data = json.loads(out.stdout or '{}')
    v = next((s for s in data.get('streams') or [] if s.get('codec_type') == 'video'), None)
    if not v:
        raise ValueError('El archivo no tiene imagen de vídeo')
    w, h = int(v.get('width') or 0), int(v.get('height') or 0)
    rot = 0
    try:
        rot = int((v.get('tags') or {}).get('rotate') or 0)
        for sd in v.get('side_data_list') or []:
            if 'rotation' in sd:
                rot = int(sd['rotation'])
    except (TypeError, ValueError):
        pass
    if abs(rot) in (90, 270):
        w, h = h, w
    dur = float((data.get('format') or {}).get('duration') or v.get('duration') or 0)
    orient = 'vertical' if h > w else 'horizontal' if w > h else 'cuadrado'
    return {'duration': round(dur, 2), 'width': w, 'height': h, 'orientation': orient, 'codec': v.get('codec_name') or '',
            'format': (data.get('format') or {}).get('format_name') or ''}


def check(info, size):
    """Errores (no se puede usar) y avisos por red."""
    errors, warns = [], []
    d = info['duration']
    if size > MAX_BYTES:
        errors.append('Pesa %d MB: el máximo es 300 MB' % (size // (1024 * 1024)))
    if d < 3:
        errors.append('Dura menos de 3 segundos: ninguna red lo admite')
    if info['codec'] not in ('h264', 'hevc'):
        warns.append('El vídeo usa el códec «%s»; las redes prefieren H.264' % info['codec'])
    if d > 15 * 60:
        warns.append('Instagram: más de 15 minutos, no admite Reels tan largos')
    if d > 10 * 60:
        warns.append('TikTok: más de 10 minutos')
    if not (info['orientation'] == 'vertical' and 3 <= d <= 90 and info['width'] >= 540 and info['height'] >= 960):
        warns.append('Facebook: no cumple Reels (vertical 9:16, 3-90 s, mínimo 540×960); se publicará como vídeo normal de la página')
    if info['orientation'] != 'vertical':
        warns.append('Instagram y TikTok lo muestran mejor en vertical 9:16')
    return errors, warns


def thumbnail(path, second=1.0):
    out = VIDEO_DIR / ('thumb_%s.jpg' % uuid4().hex)
    r = subprocess.run(['ffmpeg', '-y', '-v', 'error', '-ss', str(max(0, second)), '-i', str(path), '-frames:v', '1',
                        '-vf', "scale='min(1080,iw)':-2", '-q:v', '3', str(out)], capture_output=True, timeout=60)
    if r.returncode != 0 or not out.is_file():
        raise ValueError('No se pudo sacar la portada del vídeo')
    return str(out)


async def save_upload(aid, upload):
    """Guarda el vídeo subido por partes (sin cargarlo entero en memoria) y lo valida."""
    _ensure()
    ext = Path(upload.filename or '').suffix.lower()
    if ext not in EXTS:
        raise ValueError('Formato no admitido: sube un MP4 o MOV')
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    dest = VIDEO_DIR / ('%s%s' % (uuid4().hex, ext))
    size = 0
    with open(dest, 'wb') as f:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_BYTES:
                f.close(); dest.unlink(missing_ok=True)
                raise ValueError('El vídeo pesa más de 300 MB')
            f.write(chunk)
    try:
        info = probe(dest)
        errors, warns = check(info, size)
        if errors:
            raise ValueError('; '.join(errors))
        thumb = thumbnail(dest, min(1.0, info['duration'] / 2))
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    remove(aid)  # un vídeo por noticia (el nuevo sustituye al anterior)
    vid = db.exec_('''INSERT INTO videos(article_id,path,filename,size,duration,width,height,orientation,codec,thumb_path)
                      VALUES(?,?,?,?,?,?,?,?,?,?)''', (aid, str(dest), (upload.filename or '')[:200], size, info['duration'], info['width'],
                                                       info['height'], info['orientation'], info['codec'], thumb))
    return get(aid), warns


def get(aid):
    _ensure()
    return db.row('SELECT * FROM videos WHERE article_id=? ORDER BY position LIMIT 1', (aid,))


def set_cover(aid, second=None, image_path=None):
    v = get(aid)
    if not v:
        raise ValueError('La noticia no tiene vídeo')
    if image_path:
        thumb = image_path
    else:
        second = max(0.0, min(float(second or 0), max(0.0, (v['duration'] or 1) - 0.1)))
        thumb = thumbnail(v['path'], second)
    old = v.get('thumb_path')
    db.exec_('UPDATE videos SET thumb_path=?,cover_second=? WHERE id=?', (thumb, second or 0, v['id']))
    if old and old != thumb and Path(old).is_file() and Path(old).parent == VIDEO_DIR:
        Path(old).unlink(missing_ok=True)
    return get(aid)


def remove(aid):
    _ensure()
    for v in db.rows('SELECT * FROM videos WHERE article_id=?', (aid,)):
        for p in (v.get('path'), v.get('thumb_path')):
            if p and Path(p).is_file() and Path(p).parent == VIDEO_DIR:
                Path(p).unlink(missing_ok=True)
    db.exec_('DELETE FROM videos WHERE article_id=?', (aid,))


# ---------- enlaces firmados para la vista previa (el <video> no puede mandar la cabecera de acceso) ----------

def _secret():
    return (os.getenv('JWT_SECRET') or 'desk').encode()


def signed(vid, ttl=3600):
    exp = int(time.time()) + ttl
    sig = hmac.new(_secret(), ('%s:%s' % (vid, exp)).encode(), hashlib.sha256).hexdigest()[:32]
    return '/api/video/%s/stream?exp=%s&sig=%s' % (vid, exp, sig)


def valid_sig(vid, exp, sig):
    try:
        if int(exp) < time.time():
            return False
    except (TypeError, ValueError):
        return False
    want = hmac.new(_secret(), ('%s:%s' % (vid, exp)).encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(want, str(sig or ''))


def cleanup(days=2):
    """Borra los vídeos de noticias publicadas hace más de 2 días (la red ya tiene su copia)."""
    _ensure()
    n = 0
    for v in db.rows('''SELECT v.article_id FROM videos v JOIN articles a ON a.id=v.article_id
                        WHERE (a.status='published' AND a.updated_at<datetime('now',?)) OR a.status='rejected' ''', ('-%d day' % days,)):
        remove(v['article_id']); n += 1
    return n
