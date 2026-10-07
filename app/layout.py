"""Secciones (familias de color) y ajuste del titular a la plantilla de Canva.

La plantilla EAHWTjWEEnA tiene 12 páginas iguales; cada una colorea la etiqueta
de sección con el color de una familia. Se exporta solo la página de la familia.
El titular está a 74 px en una caja de 894 px: caben 4 líneas antes del resumen.
"""
import re
import unicodedata

# (familia, página de la plantilla, color fondo, color texto, palabras clave)
FAMILIES = [
    ('OBRAS', 1, '#0150FE', '#FFFFFF', ('obras', 'obra', 'urbanismo', 'infraestructura', 'movilidad', 'trafico', 'aparcamiento', 'asfaltado', 'licencia de obra', 'carril')),
    ('CIUDAD', 2, '#4F9AFC', '#FFFFFF', ('ciudad', 'ayuntamiento', 'servicios municipales', 'barrio', 'barriada', 'vecinos')),
    ('GIBRALTAR', 3, '#7249E0', '#FFFFFF', ('gibraltar', 'frontera', 'verja', 'transfronteriz', 'campo de gibraltar', 'brexit', 'trabajadores transfronterizos')),
    ('SUCESOS', 4, '#D02132', '#FFFFFF', ('sucesos', 'suceso', 'seguridad', 'policia', 'guardia civil', 'bomberos', 'emergencia', 'detenido', 'narcotrafico', 'accidente', 'incendio')),
    ('CULTURA', 5, '#E72E79', '#FFFFFF', ('cultura', 'carnaval', 'cofradia', 'semana santa', 'musica', 'teatro', 'exposicion', 'ocio', 'concierto', 'feria')),
    ('DEPORTES', 6, '#00AB4F', '#FFFFFF', ('deportes', 'deporte', 'futbol', 'baloncesto', 'atletismo', 'balona', 'club', 'campeonato')),
    ('COMERCIO', 7, '#FF8E1A', '#FFFFFF', ('comercio', 'empresa', 'hosteleria', 'turismo', 'negocio', 'tienda', 'mercado')),
    ('MEDIO AMBIENTE', 8, '#62DBD1', '#061E5C', ('medio ambiente', 'playa', 'limpieza', 'parque', 'naturaleza', 'residuo', 'alga', 'litoral', 'clima', 'reciclaje')),
    ('POLÍTICA', 9, '#08176E', '#FFFFFF', ('politica', 'elecciones', 'pleno', 'partido', 'administracion', 'alcalde', 'concejal', 'presupuesto')),
    ('SOCIEDAD', 10, '#2756CD', '#FFFFFF', ('sociedad', 'economia', 'empleo', 'vivienda', 'educacion', 'sanidad', 'asociacion', 'hospital', 'colegio', 'ayudas', 'alquiler')),
    ('PATRIMONIO', 11, '#B9831E', '#FFFFFF', ('patrimonio', 'historia', 'memoria', 'efemeride', 'arqueolog', 'bunker', 'fortificacion')),
    ('AGENDA', 12, '#FDE206', '#061E5C', ('agenda', 'planes', 'evento', 'que hacer', 'fin de semana')),
]
FAMILY_NAMES = [f[0] for f in FAMILIES]
_BY_NAME = {f[0]: f for f in FAMILIES}

# Secciones más concretas: cada una usa el color y la página de su familia (p. ej. CONCIERTOS y CARNAVAL → CULTURA)
SUBSECTIONS = {
    'OBRAS': ['URBANISMO', 'MOVILIDAD', 'TRÁFICO', 'INFRAESTRUCTURAS', 'APARCAMIENTOS'],
    'CIUDAD': ['AYUNTAMIENTO', 'BARRIADAS', 'SERVICIOS', 'VECINOS', 'LIMPIEZA VIARIA'],
    'GIBRALTAR': ['FRONTERA', 'TRANSFRONTERIZOS', 'CAMPO DE GIBRALTAR'],
    'SUCESOS': ['SEGURIDAD', 'POLICÍA', 'TRIBUNALES', 'EMERGENCIAS', 'NARCOTRÁFICO'],
    'CULTURA': ['CONCIERTOS', 'CARNAVAL', 'TEATRO', 'EXPOSICIONES', 'FIESTAS', 'FERIA', 'MÚSICA', 'CINE', 'LIBROS'],
    'DEPORTES': ['FÚTBOL', 'BALONA', 'BALONCESTO', 'ATLETISMO', 'DEPORTE BASE', 'BALONMANO', 'NÁUTICA'],
    'COMERCIO': ['EMPRESAS', 'TURISMO', 'HOSTELERÍA', 'ECONOMÍA', 'PUERTO'],
    'MEDIO AMBIENTE': ['PLAYAS', 'PARQUES', 'ANIMALES', 'NATURALEZA', 'RECICLAJE'],
    'POLÍTICA': ['PLENO', 'ELECCIONES', 'PRESUPUESTOS', 'PARTIDOS'],
    'SOCIEDAD': ['EDUCACIÓN', 'SANIDAD', 'VIVIENDA', 'EMPLEO', 'AYUDAS', 'MAYORES', 'JUVENTUD', 'IGUALDAD', 'SOLIDARIDAD'],
    'PATRIMONIO': ['HISTORIA', 'MEMORIA', 'EFEMÉRIDES', 'ARQUEOLOGÍA'],
    'AGENDA': ['PLANES', 'FIN DE SEMANA', 'EVENTOS'],
}
_LABEL_FAMILY = {}


def _plain(text):
    text = unicodedata.normalize('NFD', str(text or '').lower())
    return ''.join(c for c in text if unicodedata.category(c) != 'Mn')


def family_for(section, text=''):
    """Devuelve la familia (nombre, página, fondo, texto) para una sección, subsección o tema."""
    s = _plain(section).strip()
    for f in FAMILIES:
        if s == _plain(f[0]):
            return f
    if not _LABEL_FAMILY:
        _LABEL_FAMILY.update({_plain(lbl): fam for fam, labels in SUBSECTIONS.items() for lbl in labels})
    if s in _LABEL_FAMILY:
        return _BY_NAME[_LABEL_FAMILY[s]]
    for f in FAMILIES:  # la sección es una subsección conocida (p. ej. «Playas»)
        if s and any(s == k or s.startswith(k) for k in f[4]):
            return f
    blob = s + ' ' + _plain(text)
    for f in FAMILIES:
        if any(re.search(r'\b' + re.escape(k), blob) for k in f[4]):
            return f
    return _BY_NAME['CIUDAD']


def normalize_section(section, text=''):
    """Familia o subsección válida, en mayúsculas (la etiqueta que sale en la imagen)."""
    s = _plain(section).strip()
    for fam, labels in SUBSECTIONS.items():
        for lbl in labels:
            if s == _plain(lbl):
                return lbl
    return family_for(section, text)[0]


def all_labels():
    """[(etiqueta, familia, fondo, texto)] para el panel."""
    out = []
    for f in FAMILIES:
        out.append((f[0], f[0], f[2], f[3]))
        out += [(lbl, f[0], f[2], f[3]) for lbl in SUBSECTIONS.get(f[0], [])]
    return out


# Anchura media de cada carácter (en «em») calibrada con la fuente del titular en Canva.
_W = {' ': 0.26, '.': 0.28, ',': 0.28, ':': 0.28, ';': 0.28, '-': 0.36, "'": 0.25, '«': 0.5, '»': 0.5, '"': 0.4}


def _cw(c):
    if c in _W: return _W[c]
    if c in 'iljIJ1!¡íÍ': return 0.33
    if c in 'tf': return 0.40
    if c in 'mwMW': return 0.92
    if c.isupper(): return 0.72
    if c.isdigit(): return 0.62
    return 0.60


HEADLINE_FONT = 74
HEADLINE_BOX = 870      # 894 px de caja con margen de seguridad
HEADLINE_MAX_LINES = 4  # el titular crece hacia arriba desde la entradilla; hay plantilla para 1, 2, 3 y 4 líneas
SUMMARY_MAX = 165       # la entradilla cabe en 3 líneas (29 px, caja de 970 px, mayúsculas)


def headline_lines(text, font=HEADLINE_FONT, box=HEADLINE_BOX):
    lines, cur = [], ''
    for word in str(text or '').split():
        test = (cur + ' ' + word).strip()
        if sum(_cw(c) for c in test) * font <= box or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = word
    if cur: lines.append(cur)
    return lines


def headline_fits(text):
    return len(headline_lines(text)) <= HEADLINE_MAX_LINES


def fit_headline(text):
    """Recorta por palabras hasta que quepa en 4 líneas (último recurso)."""
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    if headline_fits(text):
        return text
    words = text.split()
    while words and not headline_fits(' '.join(words)):
        words.pop()
    out = ' '.join(words).rstrip(' ,;:-')
    # no terminar en palabra vacía
    while out and out.split()[-1].lower() in {'de', 'del', 'la', 'el', 'en', 'y', 'a', 'los', 'las', 'por', 'con', 'para', 'al', 'que'}:
        out = ' '.join(out.split()[:-1])
    return out


def fit_summary(text, limit=SUMMARY_MAX):
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    end = cut.rfind('. ')
    return cut[:end + 1] if end > limit * 0.5 else cut.rsplit(' ', 1)[0].rstrip(' ,;:') + '.'
