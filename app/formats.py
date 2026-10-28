"""Formato de cada noticia: publicación normal, carrusel o vídeo.

- En Ordenar se propone un formato con su motivo (reglas rápidas, sin coste).
- Al redactar, la IA confirma si conviene carrusel y explica por qué (va en la misma petición de redacción: sin coste extra).
- Tú siempre puedes cambiarlo; lo que eliges a mano no lo vuelve a tocar nadie.
- Los carruseles tienen estado de producción: pendiente, en diseño, terminado.
"""
import re

from . import db

FORMATS = {'post': 'Publicación normal', 'carousel': 'Carrusel', 'video': 'Vídeo'}
STATES = {'pendiente': 'Pendiente', 'en_diseno': 'En diseño', 'terminado': 'Terminado'}
RULES = [
    (r'antes\s+y\s+despu[eé]s', 'Comparativa de antes y después'),
    (r'cortes?\s+de\s+tr[aá]fico|desv[ií]os?|calles?\s+cortadas|cambios?\s+(de|en\s+la)\s+circulaci[oó]n|sentido\s+[uú]nico', 'Cambios de circulación: calles y desvíos en varias diapositivas'),
    (r'\b(fases?|etapas?)\b', 'Actuación por fases: una diapositiva por fase'),
    (r'\b(requisitos|c[oó]mo\s+(solicitar|pedir|inscribirse)|paso\s+a\s+paso|gu[ií]a|claves|todo\s+lo\s+que\s+(hay\s+que|debes)\s+saber)\b', 'Guía con pasos o requisitos'),
    (r'\b(proyecto|plan)\b[^.]{0,80}\b(actuaciones|obras|mejoras|calles|zonas)\b', 'Proyecto con varias actuaciones'),
    (r'\b(novedades|programaci[oó]n|horarios?|listado|calendario\s+de|cartel\s+de|actividades)\b', 'Listado de novedades, actividades u horarios'),
]
VIDEO = re.compile(r'\b(v[ií]deo|en\s+directo|grabaci[oó]n|reel)\b', re.I)


def suggest(title, excerpt=''):
    """(formato, motivo) propuestos para una noticia."""
    text = (title or '') + '. ' + (excerpt or '')[:900]
    if VIDEO.search(title or ''):
        return 'video', 'La noticia es o trae un vídeo'
    for rx, why in RULES:
        if re.search(rx, text, re.I):
            return 'carousel', why
    nums = {n for n in re.findall(r'\b\d[\d.,]*\b', text) if not re.fullmatch(r'20\d\d', n)}
    if len(nums) >= 4:
        return 'carousel', 'Varias cifras importantes: mejor una por diapositiva'
    return 'post', ''


def apply_suggestion(cid):
    """Al entrar una noticia: guarda la propuesta si nadie ha elegido aún."""
    c = db.row('SELECT id,title,excerpt,format_by FROM candidates WHERE id=?', (cid,))
    if not c or c.get('format_by') == 'manual':
        return
    fmt, why = suggest(c['title'], c.get('excerpt'))
    db.exec_("UPDATE candidates SET format=?,format_reason=?,format_by='sugerencia',carousel_state=CASE WHEN ?='carousel' THEN COALESCE(carousel_state,'pendiente') ELSE carousel_state END WHERE id=?",
             (fmt, why, fmt, cid))


def from_draft(cid, suitable, reason):
    """La IA, al redactar, recomienda carrusel (o no). No pisa lo que elegiste a mano."""
    c = db.row('SELECT format_by FROM candidates WHERE id=?', (cid,))
    if not c or c.get('format_by') == 'manual':
        return
    if suitable:
        db.exec_("UPDATE candidates SET format='carousel',format_reason=?,format_by='ia',carousel_state=COALESCE(carousel_state,'pendiente') WHERE id=?",
                 ((reason or 'La IA recomienda carrusel')[:300], cid))


def set_format(cid, fmt=None, state=None):
    if fmt is not None:
        if fmt not in FORMATS:
            raise ValueError('Formato no válido')
        db.exec_("UPDATE candidates SET format=?,format_by='manual',carousel_state=CASE WHEN ?='carousel' THEN COALESCE(carousel_state,'pendiente') ELSE carousel_state END WHERE id=?",
                 (fmt, fmt, cid))
    if state is not None:
        if state not in STATES:
            raise ValueError('Estado no válido')
        db.exec_('UPDATE candidates SET carousel_state=? WHERE id=?', (state, cid))
    return db.row('SELECT id,format,format_reason,format_by,carousel_state FROM candidates WHERE id=?', (cid,))
