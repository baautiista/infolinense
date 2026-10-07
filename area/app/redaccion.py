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


def _slides(raw):
    out = []
    for i, s in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(s, dict):
            continue
        title, text = _s(s.get('title'), 90), _s(s.get('text'), 320)
        if not (title or text):
            continue
        role = str(s.get('role') or ('portada' if i == 0 else 'texto')).lower()
        out.append({'role': role if role in ('portada', 'dato', 'texto', 'cierre') else 'texto', 'kicker': _s(s.get('kicker'), 40),
                    'title': title, 'text': text, 'image_hint': _s(s.get('image_hint'), 200)})
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
    if tag in ('licitacion', 'edicto', 'presupuesto') or block == 'Exclusivas':
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
slides (carrusel de 5 a 8 diapositivas; cada una con role [portada|dato|texto|cierre], kicker (antetítulo corto, opcional), title (máx. 60 caracteres), text (máx. 200 caracteres; vacío en la portada si no hace falta) e image_hint (qué imagen poner: foto real concreta, imagen del pliego, plano, render o gráfico)),
render (objeto: recommended true si un render arquitectónico de «así quedaría» haría la pieza mucho más atractiva —obras, edificios, parques, calles, paseos—; why; prompt: descripción detallada en español del render a encargar o generar —lugar, elementos del proyecto según el documento, punto de vista, hora del día, estilo fotorrealista—; basis: en qué documento o imagen basarlo),
photo_query (búsqueda de fotos reales de 3 a 6 palabras con el lugar concreto),
missing (lista de datos que convendría confirmar o buscar antes de publicar; no van en el texto).'''
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
        out['slides'] = slides_from_text(out['headline'], out['entradilla'], out['body'])
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
        'slides': ('Rehaz el carrusel de Instagram (5 a 8 diapositivas: portada, interiores con un dato cada una, cierre). '
                   'JSON {"slides": [{"role","kicker","title","text","image_hint"}]}', 2500),
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


def slides_from_text(headline, entradilla, body):
    """Carrusel básico sin IA: portada + una diapositiva por párrafo + cierre."""
    slides = [{'role': 'portada', 'kicker': '', 'title': headline[:60], 'text': '', 'image_hint': 'Foto principal'}]
    if entradilla:
        slides.append({'role': 'texto', 'kicker': '', 'title': 'La clave', 'text': entradilla[:200], 'image_hint': ''})
    for para in [p for p in re.split(r'\n\s*\n', body or '') if p.strip()][:4]:
        first = re.split(r'(?<=[.!?])\s+', para.strip())
        slides.append({'role': 'texto', 'kicker': '', 'title': first[0][:60], 'text': ' '.join(first[1:])[:200] or first[0][:200], 'image_hint': ''})
    slides.append({'role': 'cierre', 'kicker': '', 'title': 'Síguenos para más', 'text': 'Toda la actualidad del Campo de Gibraltar en Área.', 'image_hint': ''})
    return slides[:8]


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
            'instagram_copy': '%s\n\n%s\n\n%s' % (head, entr, tags), 'slides': slides_from_text(head, entr, body),
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
