"""Imágenes de los documentos de una licitación, proyecto o edicto (PDF): fotos, infografías, renders y planos.

- Se descargan los documentos del expediente (los del ATOM de la Plataforma de Contratación o los PDF enlazados en la página).
- De cada PDF se sacan las imágenes incrustadas grandes y se dibujan como imagen las páginas que son planos.
- Todo queda en el volumen (/data/docs/<artículo>/) para descargarlo desde el panel.
"""
import hashlib
import json
import re
from io import BytesIO
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image

from . import db
from .config import DOCS_DIR
from .sources import UA

MAX_BYTES = 45_000_000
_PRIORITY = re.compile(r'proyecto|memoria|plano|anexo|t[eé]cnic|render|infograf|estudio|anteproyecto|prescripciones', re.I)
_SKIP = re.compile(r'declaraci[oó]n\s+responsable|modelo\s+de\s+(proposici|oferta)|deuc|anexo\s+i\b.*oferta|resoluci[oó]n\s+de\s+adjudicaci', re.I)


def doc_links(candidate):
    """Documentos del expediente: primero los del ATOM y después los PDF de la página de origen."""
    try:
        docs = json.loads(candidate.get('docs_json') or '[]')
    except Exception:
        docs = []
    url = (candidate.get('url') or '').split('#')[0]
    if url.startswith('http') and len(docs) < 3:
        try:
            r = requests.get(url, timeout=30, headers={'User-Agent': UA})
            if 'pdf' in (r.headers.get('content-type') or '').lower():
                docs.append({'name': 'Documento', 'kind': 'PDF', 'url': url})
            else:
                soup = BeautifulSoup(r.text, 'html.parser')
                for a in soup.find_all('a', href=True):
                    href = urljoin(r.url, a['href'])
                    name = re.sub(r'\s+', ' ', a.get_text(' ', strip=True))[:160]
                    if re.search(r'\.pdf($|\?)|GetDocument|documento|descargar', href, re.I) or re.search(r'pliego|proyecto|memoria|plano', name, re.I):
                        if urlparse(href).scheme in ('http', 'https'):
                            docs.append({'name': name or href.rsplit('/', 1)[-1], 'kind': 'Enlace', 'url': href})
        except Exception:
            pass
    seen, out = set(), []
    for d in docs:
        if d.get('url') and d['url'] not in seen and not _SKIP.search(d.get('name') or ''):
            seen.add(d['url'])
            out.append(d)
    out.sort(key=lambda d: 0 if _PRIORITY.search((d.get('name') or '') + ' ' + (d.get('kind') or '')) else 1)
    return out


def _download(url):
    with requests.get(url, timeout=(15, 120), headers={'User-Agent': UA}, stream=True) as r:
        r.raise_for_status()
        data = BytesIO()
        for chunk in r.iter_content(256 * 1024):
            data.write(chunk)
            if data.tell() > MAX_BYTES:
                raise ValueError('El documento supera 45 MB')
        return data.getvalue(), (r.headers.get('content-type') or '').lower()


def _save(folder, raw, ext='jpg'):
    digest = hashlib.sha1(raw).hexdigest()[:16]
    path = folder / ('%s.%s' % (digest, ext))
    if not path.exists():
        path.write_bytes(raw)
    return path


def _keep_image(im):
    w, h = im.size
    return w >= 500 and h >= 350 and 0.25 <= w / max(1, h) <= 4


def _images_pypdf(data, folder, name, max_images=24):
    """Sin PyMuPDF: solo las imágenes incrustadas (pypdf)."""
    from pypdf import PdfReader
    out, hashes = [], set()
    reader = PdfReader(BytesIO(data))
    for pno, page in enumerate(reader.pages[:120]):
        try:
            imgs = list(page.images)
        except Exception:
            continue
        for img in imgs:
            if len(out) >= max_images:
                return out
            try:
                im = Image.open(BytesIO(img.data)).convert('RGB')
            except Exception:
                continue
            if not _keep_image(im):
                continue
            buf = BytesIO()
            im.save(buf, 'JPEG', quality=92)
            h = hashlib.sha1(buf.getvalue()).hexdigest()
            if h in hashes:
                continue
            hashes.add(h)
            out.append({'path': str(_save(folder, buf.getvalue())), 'width': im.width, 'height': im.height,
                        'caption': '%s · pág. %s' % (name, pno + 1), 'kind': 'imagen'})
    return out


def images_from_pdf(data, folder, name, max_images=24, max_plans=6):
    """Imágenes grandes incrustadas + páginas de planos dibujadas (PyMuPDF; si no está, pypdf sin planos)."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return _images_pypdf(data, folder, name, max_images)
    out, hashes = [], set()
    doc = fitz.open(stream=data, filetype='pdf')
    try:
        plans = 0
        for pno in range(min(len(doc), 120)):
            page = doc[pno]
            for info in page.get_images(full=True):
                if len(out) >= max_images:
                    break
                try:
                    pix = fitz.Pixmap(doc, info[0])
                    if pix.n - pix.alpha >= 4:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    if pix.width < 500 or pix.height < 350:
                        continue
                    raw = pix.tobytes('png')
                    im = Image.open(BytesIO(raw)).convert('RGB')
                    if not _keep_image(im):
                        continue
                    buf = BytesIO()
                    im.save(buf, 'JPEG', quality=92)
                    h = hashlib.sha1(buf.getvalue()).hexdigest()
                    if h in hashes:
                        continue
                    hashes.add(h)
                    path = _save(folder, buf.getvalue())
                    out.append({'path': str(path), 'width': im.width, 'height': im.height, 'caption': '%s · pág. %s' % (name, pno + 1), 'kind': 'imagen'})
                except Exception:
                    continue
            # Planos: páginas con muchos trazos y poco texto
            if plans < max_plans:
                try:
                    drawings = len(page.get_drawings())
                    words = len(page.get_text('words'))
                except Exception:
                    drawings, words = 0, 999
                if drawings > 400 and words < 600:
                    zoom = 1800 / max(page.rect.width, page.rect.height)
                    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                    raw = pix.tobytes('png')
                    path = _save(folder, raw, 'png')
                    plans += 1
                    out.append({'path': str(path), 'width': pix.width, 'height': pix.height, 'caption': 'Plano · %s · pág. %s' % (name, pno + 1), 'kind': 'plano'})
            if len(out) >= max_images + max_plans:
                break
    finally:
        doc.close()
    return out


def extract_for_article(article_id, max_docs=5):
    """Descarga los documentos del expediente y guarda sus imágenes como media del artículo."""
    art = db.row('SELECT * FROM articles WHERE id=?', (article_id,))
    if not art:
        raise ValueError('Noticia no encontrada')
    cand = db.row('SELECT * FROM candidates WHERE id=?', (art['candidate_id'],)) or {}
    folder = DOCS_DIR / str(article_id)
    folder.mkdir(parents=True, exist_ok=True)
    links = doc_links(cand)
    found, errors, read = 0, [], 0
    for d in links:
        if read >= max_docs:
            break
        try:
            data, ctype = _download(d['url'])
        except Exception as exc:
            errors.append('%s: %s' % (d.get('name'), str(exc)[:120]))
            continue
        read += 1
        try:
            if data[:4] == b'%PDF' or 'pdf' in ctype:
                imgs = images_from_pdf(data, folder, d.get('name') or 'Documento')
            elif ctype.startswith('image/'):
                im = Image.open(BytesIO(data)).convert('RGB')
                buf = BytesIO()
                im.save(buf, 'JPEG', quality=92)
                imgs = [{'path': str(_save(folder, buf.getvalue())), 'width': im.width, 'height': im.height, 'caption': d.get('name') or 'Imagen', 'kind': 'imagen'}]
            else:
                imgs = []
        except Exception as exc:
            errors.append('%s: %s' % (d.get('name'), str(exc)[:120]))
            continue
        for im in imgs:
            if db.row('SELECT 1 FROM media WHERE article_id=? AND local_path=?', (article_id, im['path'])):
                continue
            db.exec_('INSERT INTO media(article_id,kind,url,source,local_path,width,height,caption) VALUES(?,?,?,?,?,?,?,?)',
                     (article_id, 'plano' if im['kind'] == 'plano' else 'doc', d['url'], d.get('name') or '', im['path'],
                      im['width'], im['height'], im['caption']))
            found += 1
    return {'documents': [{'name': d.get('name'), 'url': d.get('url'), 'kind': d.get('kind')} for d in links],
            'documents_read': read, 'images': found, 'errors': errors}
