"""Lee el texto de las imágenes terminadas (OCR con Tesseract) para unir cada imagen con su noticia por el titular."""
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata

_STOP = {'de', 'del', 'la', 'las', 'el', 'los', 'en', 'y', 'a', 'al', 'por', 'con', 'para', 'que', 'se', 'un', 'una',
         'su', 'sus', 'lo', 'es', 'o', 'e', 'tras', 'sobre', 'como', 'mas'}


def available():
    return bool(shutil.which('tesseract'))


def _langs():
    try:
        out = subprocess.run(['tesseract', '--list-langs'], capture_output=True, text=True, timeout=10).stdout
        return 'spa+eng' if 'spa' in out.split() else 'eng'
    except Exception:
        return 'eng'


def read_text(im):
    """Texto de la imagen (vacío si no hay Tesseract o falla)."""
    if not available():
        return ''
    from PIL import ImageOps
    g = ImageOps.grayscale(im.convert('RGB'))
    if g.width > 1400:
        g = g.resize((1400, round(g.height * 1400 / g.width)))
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        g.save(f, 'PNG'); path = f.name
    try:
        return subprocess.run(['tesseract', path, '-', '-l', _langs(), '--psm', '11'],
                              capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return ''
    finally:
        os.unlink(path)


def words(text):
    t = unicodedata.normalize('NFKD', str(text or '').lower())
    t = ''.join(c for c in t if not unicodedata.combining(c))
    return {w for w in re.findall(r'[a-z0-9]+', t) if (len(w) > 2 or w.isdigit()) and w not in _STOP}


def score(headline, text):
    h = words(headline)
    return len(h & words(text)) / len(h) if h else 0.0


def match(headlines, texts, minimum=0.35):
    """Empareja titulares con textos leídos: devuelve {índice de titular: índice de imagen}, el mejor primero."""
    pairs = sorted(((score(h, t), i, j) for i, h in enumerate(headlines) for j, t in enumerate(texts) if t), reverse=True)
    out, used = {}, set()
    for s, i, j in pairs:
        if s < minimum:
            break
        if i not in out and j not in used:
            out[i] = j; used.add(j)
    return out
