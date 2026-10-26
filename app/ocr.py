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


def _run(img, psm='11'):
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        img.save(f, 'PNG'); path = f.name
    try:
        return subprocess.run(['tesseract', path, '-', '-l', _langs(), '--psm', psm],
                              capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return ''
    finally:
        os.unlink(path)


def read_text(im):
    """Texto de la imagen (vacío si no hay Tesseract). Se lee la zona de abajo (sección, titular y entradilla)
    dos veces: tal cual (letra oscura sobre blanco) e invertida (letra blanca sobre color)."""
    if not available():
        return ''
    from PIL import ImageOps
    g = ImageOps.grayscale(im.convert('RGB'))
    w, h = g.size
    crop = g.crop((0, int(h * 0.35), w, int(h * 0.93)))
    scale = 1600 / max(1, crop.width)
    crop = crop.resize((1600, max(1, round(crop.height * scale))))
    return _run(crop) + '\n' + _run(ImageOps.invert(crop))


def section_in(text):
    """Etiqueta de sección escrita en la imagen (en mayúsculas), p. ej. «CIUDAD» o «MEDIO AMBIENTE»."""
    from . import layout
    plain = unicodedata.normalize('NFKD', str(text or ''))
    plain = ''.join(c for c in plain if not unicodedata.combining(c))
    best = ''
    for label, *_ in layout.all_labels():
        lp = ''.join(c for c in unicodedata.normalize('NFKD', label) if not unicodedata.combining(c))
        if re.search(r'(?<![A-Za-z])' + re.escape(lp).replace('\\ ', r'\s+') + r'(?![A-Za-z])', plain) and len(lp) > len(best):
            best = label
    return best


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


def _family_by_color(im):
    """Familia por el color de la etiqueta: busca una franja larga y lisa de uno de los colores de sección
    (las letras del titular no forman franjas largas)."""
    from . import layout
    rgb = im.convert('RGB')
    w, h = rgb.size
    if w != 1080:
        rgb = rgb.resize((1080, round(h * 1080 / w))); w, h = rgb.size
    cols = [(f[0], tuple(int(f[2][k:k + 2], 16) for k in (1, 3, 5))) for f in layout.FAMILIES]
    px = rgb.load()
    best = {}
    for y in range(int(h * 0.36), int(h * 0.66), 3):
        run_fam, run = None, 0
        for x in range(0, w // 2):
            c = px[x, y]
            fam = next((n for n, t in cols if abs(c[0] - t[0]) + abs(c[1] - t[1]) + abs(c[2] - t[2]) < 40), None)
            if fam and fam == run_fam:
                run += 1
            else:
                run_fam, run = fam, 1 if fam else 0
            if fam:
                best[fam] = max(best.get(fam, 0), run)
    fam, length = max(best.items(), key=lambda kv: kv[1], default=(None, 0))
    return fam if length >= 120 else ''


def section_of(im, text=''):
    """Sección que muestra la imagen: el color de la etiqueta da la familia; si el texto leído
    trae una subsección de esa familia (p. ej. PLAYAS), se usa esa."""
    from . import layout
    fam = _family_by_color(im)
    label = section_in(text)
    if label and (not fam or layout.family_for(label)[0] == fam):
        return label
    return fam
