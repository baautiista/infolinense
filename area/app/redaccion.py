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


LAYOUTS = ['portada', 'lista', 'caja', 'flujo', 'cifra_lista', 'ficha', 'pregunta', 'anotada', 'mapa', 'cifra', 'tarjetas', 'mosaico', 'documento', 'calles']
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
- calles: listado de calles o lugares y qué se hace en cada uno: title («Estas son las calles que cambian»), text opcional y
  cards {label: nombre de la calle, text: qué se hace allí: «se cambia la tubería de agua», «nuevo asfaltado y aceras»}; hasta 6 por
  diapositiva (si hay más, usa otra diapositiva calles con el resto).
La PORTADA lleva exactamente el titular y la entradilla de la pieza.
En title, text y bullets marca con ==así== las 1-3 palabras clave que van resaltadas en rojo (no abuses). Textos breves: es Instagram.
CÓMO COMBINAR según la noticia (orientativo, adapta a lo que haya en la fuente):
- OBRA en un parque, plaza, edificio o equipamiento: portada → una diapositiva principal de «¿Qué se va a hacer?» (flujo o lista) →
  una lista con barras de lo que se mejora → dos diapositivas más que detallen los elementos concretos (juegos, pavimentos, sombras,
  redes, alumbrado…; cifra_lista, caja o anotada) → ficha con importe, plazo y empresa → pregunta si hay una duda clara del vecino.
- OBRAS EN VARIAS CALLES: portada → calles (cada calle con lo que se hace en ella; varias diapositivas si hacen falta) → mapa si hay
  plano → ficha con importe y plazo.
- LICITACIÓN o ADJUDICACIÓN de servicios o suministros: portada → ficha → caja (qué incluye) → documento (lo que dice el pliego) → cifra.
- AGENDA o PROGRAMACIÓN: portada → tarjetas o mosaico (una cita por tarjeta: día, hora, lugar) → lista con lo imprescindible.
- DATOS, PRESUPUESTOS o NACIONALES ADAPTADAS: portada → cifra → tarjetas → lista o documento."""


def _lst(v, n, limit):
    if isinstance(v, str):
        v = [x for x in v.split('\n')]
    return [_s(x, limit) for x in (v or []) if _s(x, limit)][:n]


def _cards(v, n=5):
    out = []
    for c in (v or [])[:n]:
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
        if layout == 'calles':
            sl['cards'] = _cards(s.get('cards'), 8)
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
headline (TITULAR breve: lo ideal entre 45 y 85 caracteres, que en la portada son 2-4 líneas; si la noticia no da para más, 2 líneas está bien; nunca tan largo que ocupe 5 líneas, unos 100 caracteres. Cuenta la noticia con el dato fuerte: «Así será el nuevo centro comercial que abrirá en 2027», «Algeciras adjudica por 374.000 euros la reforma del parque María Cristina»),
headline_options (lista de 5 titulares alternativos distintos, mismos criterios),
entradilla (ENTRADILLA: NUNCA repite el titular ni sus palabras; especifica la noticia con los datos principales que el titular no dice —dónde, quién, cuánto, plazo, empresa—, en 1-2 frases y máximo 200 caracteres. Ejemplo: titular «Así será el nuevo centro comercial que abrirá en 2027» → entradilla «En La Línea, el Ayuntamiento ha aprobado una partida de 3 millones y 12 meses de obra para transformar la antigua zona comercial»),
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
    fix_head_entr(out, candidate)
    if len(out['slides']) < 3:
        out['slides'] = slides_from_text(out['headline'], out['entradilla'], out['body'], candidate)
    elif out['slides'][0]['layout'] == 'portada':
        # La portada lleva exactamente el titular y la entradilla
        out['slides'][0]['title'], out['slides'][0]['text'] = out['headline'], out['entradilla']
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


# ---------------------------------------------------------------- titular y entradilla

HEAD_IDEAL = (45, 85)      # 2-4 líneas en la portada (orientativo)
HEAD_MAX = 100             # a partir de aquí serían 5 líneas: demasiado
_VERB = {'PUB': 'licita', 'ADJ': 'adjudica', 'RES': 'formaliza', 'MENOR': 'contrata', 'PRE': 'prepara', 'EV': 'estudia las ofertas para',
         'ANUL': 'anula'}


def _words(t):
    t = re.sub(r'[^a-z0-9ñáéíóúü ]', ' ', str(t or '').lower())
    return {w for w in t.split() if len(w) > 3}


def too_similar(a, b):
    """¿La entradilla repite el titular?"""
    a, b = str(a or '').strip(), str(b or '').strip()
    if not a or not b:
        return True
    if b.lower().startswith(a.lower()[:40]) or a.lower() in b.lower():
        return True
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= 0.7


def euros_text(amount, value=0):
    """«374.094 euros», «1,2 millones de euros»."""
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        v = 0
    if not v:
        m = re.search(r'([\d.]+),?(\d*)', str(amount or ''))
        if m:
            try:
                v = float(m.group(1).replace('.', '') + '.' + (m.group(2) or '0'))
            except ValueError:
                v = 0
    if not v:
        return ''
    if v >= 1_000_000:
        n = ('%.1f' % (v / 1_000_000)).replace('.', ',').replace(',0', '')
        return '%s millones de euros' % n
    return '{:,.0f} euros'.format(v).replace(',', '.')


def _object(title):
    """«Reconstrucción del parque infantil María Cristina» → «la reconstrucción del parque infantil María Cristina»."""
    t = re.sub(r'^[^:]{3,40}:\s*', '', str(title or '')).strip().rstrip('.')
    t = re.sub(r'^(contrato|expediente|licitaci[oó]n|procedimiento|servicio de|suministro de)\s+(de|para|del)?\s*', lambda m: m.group(0) if 'servicio' in m.group(0).lower() or 'suministro' in m.group(0).lower() else '', t, flags=re.I)
    if not t:
        return ''
    first = t.split()[0].lower()
    art = 'el' if first.endswith(('o', 'or', 'miento', 'aje')) or first in ('servicio', 'suministro', 'proyecto', 'plan', 'mantenimiento', 'asfaltado') else \
          'los' if first.endswith('os') else 'las' if first.endswith('as') else 'la'
    return '%s %s%s' % (art, t[0].lower(), t[1:])


def tender_headline(c):
    town = (c.get('towns') or '').split(',')[0] or 'Campo de Gibraltar'
    verb = _VERB.get(c.get('tstatus') or 'PUB', 'licita')
    money = euros_text(c.get('amount'), c.get('amount_value'))
    obj = _object(c.get('title'))
    head = '%s %s%s %s' % (town if town != 'Campo de Gibraltar' else 'La Mancomunidad', verb, (' por ' + money) if money else '', obj)
    return re.sub(r'\s+', ' ', head).strip()[:HEAD_MAX + 20]


def facts_entradilla(c, headline=''):
    """Entradilla con los datos que NO están en el titular: dónde, quién, plazo, empresa, tipo de contrato."""
    ex = str(c.get('excerpt') or '')
    town = (c.get('towns') or '').split(',')[0]
    org = re.sub(r'^Junta de Gobierno Local del\s+', '', c.get('organism') or '').strip()
    bits = []
    winner = c.get('winner') or (re.search(r'Adjudicataria:\s*([^·]+)', ex).group(1).strip() if re.search(r'Adjudicataria:\s*([^·]+)', ex) else '')
    kind = (re.search(r'·\s*(Obras|Servicios|Suministros|Concesión de servicios|Concesión de obras)\s*·', ex) or [None, ''])[1]
    lead = ('En %s, ' % town) if town and town.lower() not in (headline or '').lower() else ''
    who = org or 'la administración'
    if org and not re.match(r'(el|la|los|las)\s', org, re.I):
        fem = re.match(r'(junta|mancomunidad|autoridad|agencia|empresa|sociedad|diputaci|consejer|direcci|delegaci|universidad|fundaci|confederaci|demarcaci|subdelegaci)', org, re.I)
        who = ('la ' if fem else 'el ') + org
    if winner:
        bits.append('%s%s ha elegido a %s para ejecutar este contrato%s' % (lead, who, winner, (' de ' + kind.lower()) if kind else ''))
    elif c.get('tstatus') in ('PUB', None, '') and c.get('deadline'):
        d = c['deadline']
        try:
            from datetime import date
            dd = date.fromisoformat(d[:10]); d = '%s de %s' % (dd.day, ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'][dd.month - 1])
        except Exception:
            pass
        bits.append('%s%s abre el plazo para presentar ofertas hasta el %s%s' % (lead, who, d, (' en este contrato de ' + kind.lower()) if kind else ''))
    else:
        bits.append('%s%s publica el expediente en la Plataforma de Contratación del Sector Público' % (lead, who))
    plazo = re.search(r'(\d+)\s*(meses|mes|semanas|días|años)', ex + ' ' + str(c.get('title') or ''), re.I)
    if plazo:
        bits.append('con un plazo de ejecución de %s %s' % (plazo.group(1), plazo.group(2)))
    out = ', '.join(bits).strip()
    out = out[0].upper() + out[1:] if out else ''
    return (out.rstrip('.') + '.')[:230]


def fix_head_entr(out, c):
    """Titular breve (2-4 líneas) y entradilla que no repite el titular."""
    head = out.get('headline') or ''
    if len(head) > HEAD_MAX and out.get('headline_options'):
        short = [h for h in out['headline_options'] if HEAD_IDEAL[0] <= len(h) <= HEAD_MAX]
        if short:
            out['headline_options'] = [head] + [h for h in out['headline_options'] if h != short[0]]
            out['headline'] = head = short[0]
    if len(head) > HEAD_MAX:
        out.setdefault('missing', []).insert(0, 'El titular es largo (unas 5 líneas en la portada): conviene acortarlo.')
    if too_similar(head, out.get('entradilla')):
        out['entradilla'] = facts_entradilla(c, head)
    return out


def free_draft(candidate, source_text=''):
    def tidy(v):
        v = BeautifulSoup(v or '', 'html.parser').get_text(' ', strip=True)
        return re.sub(r'\s+', ' ', v).strip(' .:;—-')
    c = candidate
    raw_title = tidy(c.get('title'))
    title = re.sub(r'^(Licitación|Adjudicación|Edicto[^:]*|Anuncio previo|Contrato formalizado|Contrato menor|En evaluación|Anulada):\s*', '', raw_title)
    excerpt = tidy(c.get('excerpt'))
    text = tidy(source_text)
    sentences = [x for x in re.split(r'(?<=[.!?])\s+', text) if 60 <= len(x) <= 320][:6]
    town = (c.get('towns') or 'Campo de Gibraltar').split(',')[0]
    tender = c.get('block') == 'Licitaciones' or c.get('tag') == 'licitacion'
    head = tender_headline(c) if tender else title[:HEAD_MAX]
    entr = facts_entradilla(c, head) if tender else ''
    if not entr or too_similar(head, entr):
        cand = [x for x in [excerpt] + sentences if x and not too_similar(head, x)]
        entr = (cand[0][:217] + '…') if cand and len(cand[0]) > 220 else (cand[0] if cand else facts_entradilla(c, head))
    body = '\n\n'.join(dict.fromkeys([x for x in [entr, excerpt] + sentences if x]))[:2000]
    tags = ' '.join(['#CampoDeGibraltar', HASHTAG_TOWN.get(town, ''), '#ÁreaCampoDeGibraltar']).strip()
    return {'focus': head, 'section': 'LICITACIONES' if tender else 'SOCIEDAD', 'town': town,
            'headline': head, 'headline_options': [title] if title != head else [], 'entradilla': entr, 'body': body,
            'instagram_copy': '%s\n\n%s\n\n%s' % (head, entr, tags), 'slides': slides_from_text(head, entr, body, c),
            'render': {'recommended': c.get('tag') in ('obras', 'asi_sera', 'licitacion'), 'why': 'Borrador sin IA: valóralo tú.', 'prompt': '', 'basis': ''},
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
