"""Redacción de Área: titular, entradilla, texto, copy de Instagram, carrusel y propuesta de render."""
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup

from . import ai
from .ai import AIProviderError, ask_json

STYLE_FILE = Path(__file__).with_name('estilo_area.md')
SECTIONS = ['LICITACIONES', 'OBRAS', 'URBANISMO', 'PRESUPUESTOS', 'PUERTO', 'AGENDA', 'CULTURA', 'HISTORIA', 'ECONOMÍA', 'SOCIEDAD',
            'GIBRALTAR', 'POLÍTICA', 'MEDIO AMBIENTE', 'MOVILIDAD']
HASHTAG_TOWN = {'Algeciras': '#Algeciras', 'La Línea': '#LaLínea', 'San Roque': '#SanRoque', 'Los Barrios': '#LosBarrios',
                'Tarifa': '#Tarifa', 'Jimena': '#Jimena', 'Castellar': '#Castellar', 'San Martín del Tesorillo': '#SanMartínDelTesorillo'}


def style_guide():
    try:
        return STYLE_FILE.read_text(encoding='utf-8')
    except OSError:
        return 'Eres el redactor jefe de Área Campo de Gibraltar. Devuelves solo JSON válido.'


_META = re.compile(r'(no consta|no constan|se desconoce|desconocemos|no se ha (podido )?(encontrar|confirmar|precisar|detallar|especificar|concretar)|'
                   r'no se especific|no se detall|no se indic|no se precis|no ha trascendido|(la|las) fuentes? (consultada|disponible|original)|'
                   r'el documento no|según la documentación facilitada|falta(n)? (por )?confirmar|queda(n)? por confirmar|la información disponible|sin más detalles)', re.I)


def clean_meta(text):
    out = []
    for para in re.split(r'\n\s*\n', str(text or '')):
        kept = [x for x in re.split(r'(?<=[.!?])\s+', para.strip()) if x and not _META.search(x)]
        if kept:
            out.append(' '.join(kept))
    return '\n\n'.join(out).strip()


def _s(value, limit):
    return re.sub(r'[ \t]+', ' ', str(value or '')).strip()[:limit]


LAYOUTS = ['portada', 'lista', 'caja', 'flujo', 'cifra_lista', 'ficha', 'pregunta', 'anotada', 'mapa', 'cifra', 'tarjetas', 'mosaico', 'documento']
_OLD_ROLES = {'portada': 'portada', 'dato': 'lista', 'texto': 'lista', 'cierre': 'lista'}

CAROUSEL_GUIDE = """CARRUSEL (estilo de Área, 1080x1350). La PRIMERA diapositiva es SIEMPRE la portada; el resto se COMBINA según lo que pida
cada noticia (no repitas siempre la misma fórmula ni el mismo orden). Tipos disponibles (layout):
- portada: foto a sangre con el titular en mayúsculas (title, máx. 90 caracteres, con el dato fuerte: «ADJUDICADA POR 4,5 MILLONES LA GRAN
  REMODELACIÓN DE LA PLAZA…») y una entradilla corta (text, máx. 110: «Eiffage ejecutará una transformación integral durante 12 meses»).
- lista: pregunta o idea como título («¿Por qué se actúa ahora?», «Más sombra, más verde y más estancia»), subtítulo (text) y 2-4 puntos (bullets).
- caja: título + subtítulo + lista de lo que incluye dentro de una caja roja (bullets de 2-5 palabras, hasta 8): «¿Qué cambiará en la plaza?».
- flujo: título, subtítulo, 2-3 etiquetas encadenadas con flechas (chips, 1-2 palabras cada una: «Pavimentos nuevos → Plataforma única →
  Prioridad peatonal») y 2-3 frases (bullets).
- cifra_lista: título, subtítulo, 2-3 puntos y una cifra al pie en píldora roja (figure «48.045 €» + figure_label «en mejoras sin coste…»).
- ficha: un proyecto concreto: title (nombre), text (explicación), status («Ejecutado», «En licitación», «Adjudicado», «En obras»…) y figure («4,5 M. €»).
- pregunta: fondo negro, la duda del vecino como título («¿Un trabajador de Gibraltar puede aparcar gratis?») y la respuesta (text).
- anotada: foto con 2-3 rótulos y flechas (bullets cortos: «Mallado de agua potable con nuevas tuberías»); kicker = frase de contexto; title.
- mapa: título + texto con una imagen insertada (mapa de la zona, plano o render del pliego).
- cifra: estilo claro con una cifra gigante: kicker («¿Cómo se ha llegado hasta aquí?»), figure («+300 M€»), figure_label («de deuda
  amortizada»), text (explicación de 1-2 frases).
- tarjetas: estilo claro: kicker, title («De amortizar deuda a financiar la ciudad») y 2-4 cards {label, figure, text, icon}
  (icon: euro, obras, familia, casa, calendario, agua, arbol, coche, persona, barco, luz, check).
- mosaico: estilo claro: kicker y 2-5 cards {label, figure, text} con una foto cada una (varios proyectos o actuaciones).
- documento: estilo claro, cuando la fuente es un documento oficial (pliego, informe, decreto): kicker («LO CERTIFICA»), title
  («INTERVENCIÓN», «EL PLIEGO», «EL BOP»), text (qué dice exactamente) y se muestra el documento.
En title, text y bullets marca con ==así== las 1-3 palabras clave que van resaltadas en rojo (no abuses). Textos breves: es Instagram.
Para LICITACIONES, una buena combinación suele ser: portada → ficha (importe y estado) → caja o flujo (qué incluye la obra) → lista
(¿por qué?/¿qué cambia?) → documento (lo que dice el pliego) o pregunta (dudas del vecino) → cifra (plazo o importe). Elige la que encaje."""


def _lst(v, n, limit):
    if isinstance(v, str):
        v = [x for x in v.split('\n')]
    return [_s(x, limit) for x in (v or []) if _s(x, limit)][:n]


def _cards(v):
    out = []
    for c in (v or [])[:5]:
        if isinstance(c, str):
            parts = [x.strip() for x in c.split('|')] + ['', '', '', '']
            c = {'label': parts[0], 'figure': parts[1], 'text': parts[2], 'icon': parts[3]}
        if isinstance(c, dict) and (c.get('label') or c.get('text') or c.get('figure')):
            out.append({'label': _s(c.get('label'), 60), 'figure': _s(c.get('figure'), 30), 'text': _s(c.get('text'), 160), 'icon': _s(c.get('icon'), 20)})
    return out


def _slides(raw):
    out = []
    for i, s in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(s, dict):
            continue
        layout = str(s.get('layout') or _OLD_ROLES.get(str(s.get('role') or '').lower()) or ('portada' if i == 0 else 'lista')).lower()
        layout = layout if layout in LAYOUTS else 'lista'
        sl = {'layout': layout, 'kicker': _s(s.get('kicker'), 80), 'title': _s(s.get('title'), 140), 'text': _s(s.get('text'), 360),
              'bullets': _lst(s.get('bullets'), 8, 160), 'chips': _lst(s.get('chips'), 4, 30), 'figure': _s(s.get('figure'), 30),
              'figure_label': _s(s.get('figure_label'), 90), 'status': _s(s.get('status'), 30), 'cards': _cards(s.get('cards')),
              'image_hint': _s(s.get('image_hint'), 200)}
        if any(sl[k] for k in ('title', 'text', 'bullets', 'figure', 'cards', 'kicker')):
            out.append(sl)
    # La portada siempre va primero
    covers = [x for x in out if x['layout'] == 'portada']
    rest = [x for x in out if x['layout'] != 'portada']
    if covers:
        out = [covers[0]] + rest
    elif out:
        out[0]['layout'] = 'portada'
    return out[:10]


def _kind_rules(candidate):
    block, tag = candidate.get('block') or '', candidate.get('tag') or ''
    if block == 'Nacionales adaptables':
        return ('PIEZA NACIONAL ADAPTADA: convierte la noticia nacional en una pieza del Campo de Gibraltar («Así ha cambiado X en el Campo de Gibraltar»). '
                'Usa SOLO cifras de municipios de la comarca que aparezcan en el texto fuente; si la fuente no las trae, escribe la pieza con lo que sí '
                'aplica a la comarca y apunta en missing qué dato municipal habría que buscar (por ejemplo, la tabla del INE). Nunca inventes cifras municipales.')
    if tag == 'efemeride':
        return ('EFEMÉRIDE: pieza de historia con gancho actual («Tal día como hoy…», «El día en que…», «Se cumplen N años…»). Usa solo hechos '
                'históricos bien establecidos; ante la duda, no lo afirmes y anótalo en missing para verificar.')
    if (candidate.get('url') or '').startswith('resumen://'):
        return ('RESUMEN DE LICITACIONES: pieza «Lo que licita…/Las licitaciones de la semana en…» con todas las del EXTRACTO. Titular con el total '
                'y el número de contratos. En el carrusel usa un mosaico o tarjetas y una diapositiva por licitación importante (ficha), '
                'y cierra con una cifra (importe total).')
    if tag in ('licitacion', 'edicto', 'presupuesto') or block in ('Exclusivas', 'Licitaciones'):
        return ('EXCLUSIVA DOCUMENTAL: cuenta el documento como noticia (qué se va a hacer, dónde, por cuánto, en qué plazo, qué empresa si está '
                'adjudicado, qué fase administrativa es). Si es una obra o un proyecto, valora un «Así será…». Destaca en el carrusel el importe, '
                'el plazo y lo que cambia para el vecino.')
    if tag == 'agenda' or block == 'Agenda y cultura':
        return ('AGENDA: el carrusel debe servir para guardarlo: una diapositiva por día o por cita, con fecha, hora, lugar y precio si constan.')
    if tag in ('obras', 'asi_sera'):
        return 'OBRAS Y URBANISMO: explica qué cambia en ese lugar, antes y después, plazos e inversión.'
    return ''


def draft(candidate, source_text='', docs_text=''):
    """Redacta la pieza completa. Sin IA, borrador básico a partir de la fuente."""
    if not ai.AI_ENABLED:
        return free_draft(candidate, source_text)
    towns = candidate.get('towns') or ''
    area_note = ''
    if candidate.get('area_state') == 'update':
        area_note = ('ATENCIÓN: Área ya publicó «%s». Esta pieza debe ser una ACTUALIZACIÓN: el titular y la entradilla cuentan lo NUEVO '
                     '(nueva fase, nueva cifra, nueva fecha), no lo ya publicado.' % (candidate.get('area_title') or ''))
    links = candidate.get('_links') or []
    others = '\n'.join('- %s: %s' % (l.get('outlet') or l.get('source_name') or '', l.get('title') or '') for l in links[:6])
    prompt = f'''{_kind_rules(candidate)}
{area_note}
Escribe la pieza para Área Campo de Gibraltar siguiendo la guía. Aprovecha TODO el texto fuente (cifras, plazos, lugares, empresas, fases), no solo el titular.

PROPUESTA: {candidate.get('title', '')}
BLOQUE: {candidate.get('block', '')} · ETIQUETA: {candidate.get('tag', '')} · MUNICIPIOS DETECTADOS: {towns or 'ninguno'}
ORGANISMO: {candidate.get('organism') or ''} · IMPORTE: {candidate.get('amount') or ''} · PLAZO: {candidate.get('deadline') or ''}
FUENTE: {candidate.get('source_name', '')} · {candidate.get('url', '')} · FECHA: {candidate.get('published_at') or ''}
EXTRACTO: {(candidate.get('excerpt') or '')[:1500]}
OTRAS FUENTES DE LO MISMO:
{others}
TEXTO FUENTE: {(source_text or '')[:12000]}
DOCUMENTOS DEL EXPEDIENTE (texto extraído): {(docs_text or '')[:5000]}

Devuelve JSON con exactamente estas claves:
focus (la verdadera noticia en una frase),
section (una de: {', '.join(SECTIONS)}),
town (municipio principal; «Campo de Gibraltar» si es comarcal),
headline (titular, máximo 90 caracteres),
headline_options (lista de 5 titulares alternativos distintos, mismos criterios),
entradilla (máximo 220 caracteres, información nueva respecto al titular),
body (texto de la noticia para la web: entre 1.200 y 2.000 caracteres, párrafos de 2-4 frases separados por línea en blanco),
instagram_copy (copy para Instagram según la guía, con hashtags al final),
slides (carrusel de 4 a 8 diapositivas según la guía de CARRUSEL de abajo; cada una con layout y sus campos —kicker, title, text, bullets, chips, figure, figure_label, status, cards— más image_hint: qué imagen poner en esa diapositiva: foto real concreta, imagen o plano del pliego, render, mapa o el documento),
render (objeto: recommended true si un render arquitectónico de «así quedaría» haría la pieza mucho más atractiva —obras, edificios, parques, calles, paseos—; why; prompt: descripción detallada en español del render a encargar o generar —lugar, elementos del proyecto según el documento, punto de vista, hora del día, estilo fotorrealista—; basis: en qué documento o imagen basarlo),
photo_query (búsqueda de fotos reales de 3 a 6 palabras con el lugar concreto),
missing (lista de datos que convendría confirmar o buscar antes de publicar; no van en el texto).

{CAROUSEL_GUIDE}'''
    data, provider, _ = ask_json(style_guide(), prompt, max_tokens=6000)
    if not isinstance(data, dict) or not data.get('headline'):
        raise AIProviderError('La IA devolvió un borrador incompleto. Reintenta.', provider, kind='empty')
    out = {
        'focus': _s(data.get('focus'), 300),
        'section': (str(data.get('section') or '').upper().strip() if str(data.get('section') or '').upper().strip() in SECTIONS else 'SOCIEDAD'),
        'town': _s(data.get('town'), 60) or (towns.split(',')[0] if towns else 'Campo de Gibraltar'),
        'headline': clean_meta(_s(data.get('headline'), 140)) or _s(data.get('headline'), 140),
        'headline_options': [_s(h, 140) for h in (data.get('headline_options') or []) if _s(h, 140)][:6],
        'entradilla': clean_meta(_s(data.get('entradilla'), 300)) or _s(data.get('entradilla'), 300),
        'body': clean_meta(str(data.get('body') or ''))[:3000],
        'instagram_copy': str(data.get('instagram_copy') or '').strip()[:2200],
        'slides': _slides(data.get('slides')),
        'render': data.get('render') if isinstance(data.get('render'), dict) else {'recommended': False},
        'photo_query': _s(data.get('photo_query'), 120),
        'missing': [_s(m, 200) for m in (data.get('missing') or []) if _s(m, 200)][:8],
        'provider': provider,
    }
    if len(out['slides']) < 3:
        out['slides'] = slides_from_text(out['headline'], out['entradilla'], out['body'], candidate)
    return out


def regenerate(article, what, extra=''):
    """Rehace una parte: headlines | slides | instagram | render."""
    if not ai.AI_ENABLED:
        raise AIProviderError('Para rehacer esta parte hace falta una clave de IA (GEMINI_API_KEY gratis).', kind='config')
    base = f'''TITULAR: {article.get('headline', '')}
ENTRADILLA: {article.get('entradilla', '')}
TEXTO: {(article.get('body') or '')[:2600]}
INDICACIONES DEL EDITOR: {extra or 'ninguna'}'''
    asks = {
        'headlines': ('Propón 6 titulares nuevos y distintos (máx. 90 caracteres, con el dato fuerte). JSON {"headline_options": [...]}', 1200),
        'slides': ('Rehaz el carrusel de Instagram combinando los tipos de diapositiva que mejor cuenten esta noticia.\n' + CAROUSEL_GUIDE +
                   '\nJSON {"slides": [{"layout","kicker","title","text","bullets","chips","figure","figure_label","status","cards","image_hint"}]}', 3500),
        'instagram': ('Rehaz el copy de Instagram según la guía. JSON {"instagram_copy": "..."}', 1500),
        'render': ('Valora si conviene un render arquitectónico de «así quedaría» y descríbelo. JSON {"render": {"recommended","why","prompt","basis"}}', 1500),
    }
    if what not in asks:
        raise ValueError('Parte desconocida')
    instruction, tokens = asks[what]
    data, provider, _ = ask_json(style_guide(), instruction + '\n\n' + base, max_tokens=tokens)
    data = data if isinstance(data, dict) else {}
    if what == 'headlines':
        return {'headline_options': [_s(h, 140) for h in (data.get('headline_options') or []) if _s(h, 140)][:8]}
    if what == 'slides':
        slides = _slides(data.get('slides'))
        if len(slides) < 3:
            raise AIProviderError('La IA no devolvió un carrusel válido. Reintenta.', provider, kind='empty')
        return {'slides': slides}
    if what == 'instagram':
        return {'instagram_copy': str(data.get('instagram_copy') or '').strip()[:2200]}
    return {'render': data.get('render') if isinstance(data.get('render'), dict) else {'recommended': False}}


def slides_from_text(headline, entradilla, body, candidate=None):
    """Carrusel básico sin IA: portada, ficha si hay importe, puntos del texto."""
    c = candidate or {}
    slides = [{'layout': 'portada', 'title': headline[:110], 'text': entradilla[:120], 'image_hint': 'Foto principal o render del proyecto'}]
    if c.get('amount'):
        status = {'PUB': 'En licitación', 'ADJ': 'Adjudicada', 'RES': 'Formalizada', 'MENOR': 'Contrato menor', 'PRE': 'Anuncio previo'}.get(c.get('tstatus') or '', 'En licitación')
        slides.append({'layout': 'ficha', 'title': re.sub(r'^[^:]{3,30}:\s*', '', c.get('title') or headline)[:80], 'text': (c.get('organism') or '')[:200],
                       'status': status, 'figure': c['amount'].replace(',00 €', ' €')})
    sentences = [x for x in re.split(r'(?<=[.!?])\s+', body or '') if 30 <= len(x) <= 160][:3]
    if sentences:
        slides.append({'layout': 'lista', 'title': 'Las claves', 'bullets': sentences})
    return _slides(slides)


def free_draft(candidate, source_text=''):
    def tidy(v):
        v = BeautifulSoup(v or '', 'html.parser').get_text(' ', strip=True)
        return re.sub(r'\s+', ' ', v).strip(' .:;—-')
    title = re.sub(r'^(Licitación|Adjudicación|Edicto[^:]*|Anuncio previo|Contrato formalizado):\s*', '', tidy(candidate.get('title')))
    excerpt = tidy(candidate.get('excerpt'))
    text = tidy(source_text)
    sentences = [s for s in re.split(r'(?<=[.!?])\s+', text) if 60 <= len(s) <= 320][:6]
    town = (candidate.get('towns') or 'Campo de Gibraltar').split(',')[0]
    head = title[:140]
    entr = (excerpt[:217] + '…') if len(excerpt) > 220 else excerpt
    body = '\n\n'.join([s for s in [excerpt] + sentences if s])[:2000]
    tags = ' '.join(['#CampoDeGibraltar', HASHTAG_TOWN.get(town, ''), '#ÁreaCampoDeGibraltar']).strip()
    return {'focus': head, 'section': 'LICITACIONES' if candidate.get('tag') == 'licitacion' else 'SOCIEDAD', 'town': town,
            'headline': head, 'headline_options': [], 'entradilla': entr, 'body': body,
            'instagram_copy': '%s\n\n%s\n\n%s' % (head, entr, tags), 'slides': slides_from_text(head, entr, body, candidate),
            'render': {'recommended': candidate.get('tag') in ('obras', 'asi_sera', 'licitacion'), 'why': 'Borrador sin IA: valóralo tú.', 'prompt': '', 'basis': ''},
            'photo_query': ' '.join(title.split()[:5]), 'missing': ['Borrador sin IA: revisa y completa el texto.'], 'provider': 'source'}


def discover():
    """Con búsqueda web activada: documentos y noticias de las últimas 48 h que puedan ser exclusiva en la comarca."""
    prompt = '''Busca documentos y anuncios públicos de las últimas 48 horas sobre el Campo de Gibraltar (Algeciras, La Línea, San Roque, Los Barrios, Tarifa, Jimena, Castellar, San Martín del Tesorillo, Mancomunidad, Autoridad Portuaria de la Bahía de Algeciras):
licitaciones y adjudicaciones (Plataforma de Contratación, BOP Cádiz, BOJA, BOE), edictos, proyectos de obras, presupuestos, subvenciones, programaciones culturales completas y datos nacionales publicados por municipios que se puedan adaptar a la comarca.
Prioriza lo que los medios comarcales (Diario Área, Europa Sur, 8Directo) todavía no hayan publicado. Cada resultado debe tener una URL real que hayas visto.
Devuelve JSON {"items": [{"title","url","excerpt","published_at","block","official"}]} con máximo 12 items; block es uno de: Exclusivas, Obras y urbanismo, Agenda y cultura, Instituciones, Nacionales adaptables.'''
    data, _, _ = ask_json(style_guide(), prompt, web=True, web_required=True, max_tokens=4000)
    items = data.get('items', []) if isinstance(data, dict) else []
    return [i for i in items if isinstance(i, dict) and str(i.get('url', '')).startswith('http')]
