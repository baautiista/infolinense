import json, re, requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from .config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_WEB_SEARCH

def _extract_text(data):
    if isinstance(data,dict):
        if data.get('output_text'): return data['output_text']
        out=[]
        for item in data.get('output',[]):
            if not isinstance(item,dict): continue
            for c in item.get('content',[]):
                if isinstance(c,dict) and c.get('text'): out.append(c['text'])
        return '\n'.join(out)
    return ''

def ask(instructions,user_text,web=False):
    if not OPENAI_API_KEY: raise RuntimeError('OPENAI_API_KEY no configurada')
    payload={'model':OPENAI_MODEL,'instructions':instructions,'input':user_text}
    if web and OPENAI_WEB_SEARCH: payload['tools']=[{'type':'web_search'}]
    r=requests.post('https://api.openai.com/v1/responses',headers={'Authorization':f'Bearer {OPENAI_API_KEY}','Content-Type':'application/json'},json=payload,timeout=120)
    r.raise_for_status(); return _extract_text(r.json())

def json_from_text(t):
    t=t.strip(); t=re.sub(r'^```(?:json)?|```$','',t,flags=re.M).strip()
    try: return json.loads(t)
    except Exception:
        m=re.search(r'\{.*\}',t,re.S)
        if not m: raise
        return json.loads(m.group(0))

SYSTEM='''Eres el motor editorial de InfoLinense, medio centrado en La Línea de la Concepción. Redacta en español natural, cercano y periodístico, sin tono de gabinete ni copiar notas de prensa. Estructura: sección, titular útil, subtítulo y cuerpo breve de hasta 2.200 caracteres (el cuerpo es también el texto de redes). Prioriza lo que cambia para los vecinos: fechas, importes, plazos y administración competente, solo si constan en las fuentes. Da contexto local sin inventar hechos. No empieces el cuerpo con «La Línea de la Concepción». No uses emojis, exclamaciones, sensacionalismo ni fórmulas genéricas de IA. Cuando solo existe la fuente original, atribuye los hechos y deja claro en las notas qué falta por contrastar. Devuelve JSON válido sin markdown.'''

def draft(candidate,source_text='',research='',quick=False):
    if not OPENAI_API_KEY:
        return free_draft(candidate, source_text)
    mode='PIEZA RÁPIDA: noticia menos relevante; no hagas investigación extensa, pero no inventes.' if quick else 'PIEZA INVESTIGADA: integra contexto y contraste disponible.'
    prompt=f'''{mode}\nCANDIDATA: {candidate['title']}\nURL: {candidate.get('url','')}\nEXTRACTO: {candidate.get('excerpt','')}\nTEXTO FUENTE: {source_text[:12000]}\nINVESTIGACIÓN: {research[:8000]}\n\nDevuelve exactamente estas claves JSON:\nsection (una de URBANISMO, CIUDAD, GIBRALTAR, SUCESOS, CULTURA, DEPORTES, COMERCIO, MEDIO AMBIENTE, POLÍTICA, SOCIEDAD, PATRIMONIO, AGENDA),\nheadline (titular útil y directo),\nsubtitle (1 frase),\nbody (noticia completa, máximo 2200 caracteres),\nsocial_text (texto completo para redes, NO copy corto, máximo 2200 caracteres),\ngraphic_summary (máximo 180 caracteres, 2-3 líneas para la plantilla),\nai_image_suggestion (vacío si hay una fotografía real razonable; si no, explica qué recreación podría ser útil, sin generarla).'''
    return json_from_text(ask(SYSTEM,prompt,web=False))

def research(candidate,source_text=''):
    if not OPENAI_API_KEY:
        excerpt = BeautifulSoup(candidate.get('excerpt') or '', 'html.parser').get_text(' ', strip=True)
        excerpt = re.sub(r'\s+', ' ', excerpt)[:350]
        facts = [excerpt] if excerpt and excerpt.casefold() != (candidate.get('title') or '').casefold() else []
        return {'facts': facts, 'context': [],
                'sources': [{'name': candidate.get('source_name') or urlparse(candidate.get('url') or '').netloc or 'Fuente original', 'url': candidate.get('url') or ''}],
                'caveats': ['Extracto de la fuente original, sin contraste independiente. Comprobar fechas, cifras y contexto antes de publicar.']}
    prompt=f'''Investiga y contrasta esta posible noticia exclusivamente en relación con La Línea de la Concepción. Busca fuentes públicas actuales, dando prioridad a fuentes oficiales y documentos. No redactes aún la noticia. Devuelve JSON con: facts (lista), context (lista), sources (lista de objetos name,url), caveats (lista).\nTEMA: {candidate['title']}\nURL INICIAL: {candidate.get('url','')}\nTEXTO INICIAL: {source_text[:9000]}'''
    return json_from_text(ask(SYSTEM,prompt,web=True))

def free_draft(candidate, source_text=''):
    """Prepare an editable, source-attributed news draft without claiming verification."""
    def tidy(value):
        value = BeautifulSoup(value or '', 'html.parser').get_text(' ', strip=True)
        value = re.sub(r'[\U0001F300-\U0001FAFF\u2600-\u27BF‼¡!]', '', value)
        return re.sub(r'\s+', ' ', value).strip(' .:;—-')

    def clip(value, limit):
        value = value.strip()
        if len(value) <= limit: return value
        return value[:limit].rsplit(' ', 1)[0].rstrip(' ,;:.')

    title = tidy(candidate.get('title'))
    # Google News appends the outlet to the headline; the source is shown separately.
    title = re.sub(r'\s+[-–|]\s+(?:Europa Sur|Diario Área(?: Campo de Gibraltar)?|8Directo|Cadena SER|Canal Sur|Europa Press)\s*$', '', title, flags=re.I)
    title = clip(title, 180)
    if not title:
        raise ValueError('La noticia no tiene titular')
    excerpt = tidy(candidate.get('excerpt'))
    text = tidy(source_text)
    source = tidy(candidate.get('source_name') or urlparse(candidate.get('url') or '').netloc or 'la fuente original')
    excerpt = re.sub(r'^(?:' + re.escape(title) + r')[\s:;.,-]*', '', excerpt, flags=re.I).strip()
    # Prefer the short source summary. Page text often contains navigation and
    # other stories; only take a sentence with several headline terms.
    sentences = re.split(r'(?<=[.!?])\s+', text)
    title_terms = {w.lower() for w in re.findall(r'[\wáéíóúñü]{5,}', title)} - {'línea', 'concepción', 'ayuntamiento', 'linense', 'municipal'}
    relevant = next((clip(s, 190) for s in sentences
                     if 45 <= len(s) <= 280 and len(title_terms.intersection(
                         w.lower() for w in re.findall(r'[\wáéíóúñü]{5,}', s))) >= 2
                     and s.casefold() not in excerpt.casefold() and title.casefold() not in s.casefold()), '')
    details = [clip(s, 230).rstrip(' .') for s in re.split(r'(?<=[.!?])\s+', excerpt)
               if s.strip() and s.strip(' .').casefold() != title.casefold()]
    if not details and relevant: details = [relevant.rstrip(' .')]
    if source.casefold().startswith('ayuntamiento'):
        credit = 'según informa el Ayuntamiento de La Línea'
    elif source.casefold().startswith(('facebook', 'instagram')):
        credit = 'según la publicación original en redes sociales'
    elif source.casefold().startswith(('google news', 'noticias ·')):
        credit = 'según la fuente enlazada'
    else:
        credit = f'según publica {source}'
    title_numbers = set(re.findall(r'\d[\d.,]*', title))
    first_terms = {w.lower() for w in re.findall(r'[\wáéíóúñü]{5,}', details[0])} if details else set()
    shared_terms = len(title_terms.intersection(first_terms))
    lead_from_title = (not details or bool(title_numbers - set(re.findall(r'\d[\d.,]*', excerpt)))
                       or shared_terms < min(2, len(title_terms)))
    lead = title if lead_from_title else details[0]
    if lead.casefold().startswith('la línea de la concepción'):
        lead = credit[0].upper() + credit[1:] + ', ' + lead[0].lower() + lead[1:]
    elif 'ayuntamiento' not in lead.casefold() and not lead.casefold().startswith(('según ', 'fuente:')):
        lead += ', ' + credit
    body = lead.rstrip(' .') + '.'
    for detail in details if lead_from_title else details[1:]:
        if detail.casefold() not in body.casefold():
            body += ' ' + detail.rstrip(' .') + '.'
    if relevant and relevant.casefold() not in body.casefold() and not any(relevant.casefold() in d.casefold() for d in details):
        body += '\n\n' + relevant.rstrip(' .') + '.'
    low = (title + ' ' + excerpt).casefold()
    groups = [('GIBRALTAR', ('gibraltar', 'frontera', 'verja')), ('MEDIO AMBIENTE', ('alga', 'playa', 'litoral', 'residuos', 'medio ambiente')),
              ('URBANISMO', ('obra', 'vivienda', 'urbanismo', 'licit', 'calle', 'plaza')),
              ('DEPORTES', ('deporte', 'balona', 'club', 'campeonato')), ('CULTURA', ('cultura', 'museo', 'teatro', 'festival')),
              ('AGENDA', ('agenda', 'concierto', 'fecha')), ('COMERCIO', ('comercio', 'mercado', 'hostelería'))]
    section = next((name for name, terms in groups if any(word in low for word in terms)), 'CIUDAD')
    subtitle = clip(details[0], 180).rstrip(' .') + '.' if details else ''
    return {'section': section, 'headline': title, 'subtitle': subtitle, 'body': body[:2200],
            'social_text': body[:2200], 'graphic_summary': subtitle, 'ai_image_suggestion': ''}

def discover_candidates():
    prompt='''Busca noticias, documentos y anuncios públicos MUY RECIENTES que afecten directamente a La Línea de la Concepción. Prioriza urbanismo/obras, servicios públicos, Gibraltar o frontera cuando afecte a La Línea, agenda/cultura, comercio/aperturas, empleo/economía, contratos/licitaciones/presupuestos, patrimonio e incidencias ciudadanas. Prioriza fuentes oficiales, documentos y páginas originales. Evita cualquier resultado donde “línea” no sea la ciudad. Devuelve JSON con una clave items que sea lista de objetos: title,url,source_name,excerpt,official (boolean). Máximo 15 resultados, sin duplicados.'''
    return json_from_text(ask(SYSTEM,prompt,web=True)).get('items',[])
