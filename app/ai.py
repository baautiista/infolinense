import json, re, requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from .config import (OPENAI_API_KEY, OPENAI_MODEL, OPENAI_WEB_SEARCH,
                    ANTHROPIC_API_KEY, ANTHROPIC_MODEL, ANTHROPIC_WORKSPACE_ID,
                    ANTHROPIC_WEB_SEARCH, AI_PROVIDER)

def _configured_provider():
    if AI_PROVIDER in ('anthropic','claude'):
        return 'anthropic' if ANTHROPIC_API_KEY else ''
    if AI_PROVIDER in ('openai','gpt'):
        return 'openai' if OPENAI_API_KEY else ''
    # In automatic mode, Claude takes priority when its key is present.
    if ANTHROPIC_API_KEY: return 'anthropic'
    if OPENAI_API_KEY: return 'openai'
    return ''

ACTIVE_PROVIDER = _configured_provider()
AI_ENABLED = bool(ACTIVE_PROVIDER)

class AIProviderError(RuntimeError):
    """An AI provider rejected a request; safe to surface to the authenticated editor."""


def ask_openai(instructions, user_text, web=False):
    if not OPENAI_API_KEY:
        raise AIProviderError('ChatGPT no está conectado. Falta OPENAI_API_KEY en las variables privadas de Railway.')
    payload = {
        'model': OPENAI_MODEL,
        'instructions': instructions,
        'input': user_text,
        'max_output_tokens': 3500,
        'store': False,
    }
    if web:
        if not OPENAI_WEB_SEARCH:
            raise AIProviderError('La búsqueda web de ChatGPT está desactivada en Railway.')
        payload['tools'] = [{'type': 'web_search'}]
    try:
        response = requests.post(
            'https://api.openai.com/v1/responses',
            headers={'Authorization': f'Bearer {OPENAI_API_KEY}', 'Content-Type': 'application/json'},
            json=payload, timeout=120
        )
    except requests.RequestException as exc:
        raise AIProviderError('No se pudo contactar con la API de ChatGPT.') from exc
    if not response.ok:
        try:
            data = response.json()
            message = str((data.get('error') or {}).get('message') or '')
        except Exception:
            message = ''
        message = re.sub(r'sk-[A-Za-z0-9_-]+', '[clave oculta]', message)[:220]
        if response.status_code in (401, 403):
            detail = 'OpenAI no acepta la clave API. Revisa que OPENAI_API_KEY sea válida y tenga facturación habilitada.'
        elif response.status_code == 429:
            detail = 'OpenAI ha limitado temporalmente las peticiones o no queda saldo de API.'
        else:
            detail = f'ChatGPT rechazó la solicitud (HTTP {response.status_code})'
            if message:
                detail += ': ' + message
        raise AIProviderError(detail)
    answer = _extract_text(response.json())
    if not answer:
        raise AIProviderError('ChatGPT no devolvió texto. Inténtalo de nuevo.')
    return answer


def _check_anthropic_response(response):
    if response.ok:
        return
    try:
        payload = response.json()
    except Exception:
        payload = {}
    error = payload.get('error', {}) if isinstance(payload, dict) else {}
    message = error.get('message', '') if isinstance(error, dict) else ''
    message = re.sub(r'sk-ant-[A-Za-z0-9_-]+', '[clave oculta]', str(message))[:300]
    lower = message.lower()
    status = response.status_code

    if status == 400 and any(term in lower for term in ('spend limit', 'spending limit', 'usage limit', 'budget limit', 'monthly limit')):
        detail = 'Anthropic ha bloqueado la petición por un límite de gasto. Revisa los límites y la facturación de tu cuenta API.'
    elif 'workspace' in lower and ('required' in lower or 'id' in lower):
        detail = 'Anthropic pide el ID del espacio de trabajo. Añade ANTHROPIC_WORKSPACE_ID en las variables de Railway.'
    elif status in (401, 403):
        detail = 'Anthropic no acepta la clave API. Comprueba en Railway que ANTHROPIC_API_KEY sea una clave activa de Anthropic Console.'
    elif status == 402:
        detail = 'La cuenta API de Anthropic tiene un problema de facturación. Revisa Billing en Anthropic Console.'
    elif status == 429:
        detail = 'Anthropic ha limitado temporalmente las peticiones. Espera unos minutos y vuelve a intentarlo.'
    else:
        detail = f'Claude rechazó la solicitud (HTTP {status})'
        if message:
            detail += f': {message}'
    raise AIProviderError(detail)

def _extract_text(data):
    if isinstance(data,dict):
        if data.get('output_text'): return data['output_text']
        # Anthropic Messages API returns text blocks in content.
        blocks=data.get('content',[])
        out=[c['text'] for c in blocks if isinstance(c,dict) and c.get('type')=='text' and c.get('text')] if isinstance(blocks,list) else []
        if out: return '\n'.join(out)
        out=[]
        for item in data.get('output',[]):
            if not isinstance(item,dict): continue
            for c in item.get('content',[]):
                if isinstance(c,dict) and c.get('text'): out.append(c['text'])
        return '\n'.join(out)
    return ''
def ask(instructions,user_text,web=False):
    if not AI_ENABLED:
        raise RuntimeError('No hay una clave de IA configurada en Railway')
    if ACTIVE_PROVIDER == 'anthropic':
        payload={
            'model':ANTHROPIC_MODEL,
            'max_tokens':3500,
            'system':instructions,
            'messages':[{'role':'user','content':user_text}]
        }
        if web:
            if not ANTHROPIC_WEB_SEARCH:
                raise RuntimeError('La búsqueda web de Claude está desactivada')
            payload['tools']=[{
                'type':'web_search_20250305',
                'name':'web_search',
                'max_uses':3,
                'user_location':{
                    'type':'approximate',
                    'city':'La Línea de la Concepción',
                    'region':'Andalucía',
                    'country':'ES',
                    'timezone':'Europe/Madrid'
                }
            }]
        headers={
            'Authorization':f'Bearer {ANTHROPIC_API_KEY}',
            'anthropic-version':'2023-06-01',
            'Content-Type':'application/json'
        }
        if ANTHROPIC_WORKSPACE_ID:
            headers['anthropic-workspace-id']=ANTHROPIC_WORKSPACE_ID
        for turn in range(3):
            r=requests.post('https://api.anthropic.com/v1/messages',
                            headers=headers,json=payload,timeout=120)
            _check_anthropic_response(r)
            data=r.json()
            answer=_extract_text(data)
            if data.get('stop_reason')!='pause_turn':
                if not answer: raise RuntimeError('Claude no devolvió texto')
                return answer
            payload['messages'].append({'role':'assistant','content':data.get('content',[])})
        raise RuntimeError('Claude dejó la respuesta incompleta; inténtalo de nuevo')
    if ACTIVE_PROVIDER != 'openai':
        raise RuntimeError('Proveedor de IA no reconocido')
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
    prompt=f'''{mode}\nCANDIDATA: {candidate['title']}\nURL: {candidate.get('url','')}\nEXTRACTO: {candidate.get('excerpt','')}\nTEXTO FUENTE: {source_text[:12000]}\nINVESTIGACIÓN: {research[:8000]}\n\nDevuelve exactamente estas claves JSON:\nsection (una de URBANISMO, CIUDAD, GIBRALTAR, SUCESOS, CULTURA, DEPORTES, COMERCIO, MEDIO AMBIENTE, POLÍTICA, SOCIEDAD, PATRIMONIO, AGENDA),\nheadline (titular útil y directo),\nsubtitle (entradilla en una frase),\nbody (noticia completa, máximo 2200 caracteres),\nsocial_text (el mismo texto completo para redes, máximo 2200 caracteres),\ngraphic_summary (máximo 180 caracteres, 2-3 líneas para la plantilla),\nai_image_suggestion (vacío si hay una fotografía real razonable; si no, describe una idea sin generarla),\ncarousel_suitable (true solo si el tema requiere explicar varios pasos, cifras o consecuencias),\ncarousel_reason (motivo breve).'''
    return json_from_text(ask_openai(SYSTEM,prompt,web=False))

def _source_research(candidate,source_text=''):
    excerpt = BeautifulSoup(candidate.get('excerpt') or '', 'html.parser').get_text(' ', strip=True)
    excerpt = re.sub(r'\s+', ' ', excerpt)[:350]
    facts = [excerpt] if excerpt and excerpt.casefold() != (candidate.get('title') or '').casefold() else []
    return {'facts': facts, 'context': [],
            'sources': [{'name': candidate.get('source_name') or urlparse(candidate.get('url') or '').netloc or 'Fuente original', 'url': candidate.get('url') or ''}],
            'caveats': ['Extracto de la fuente original, sin contraste independiente. Comprobar fechas, cifras y contexto antes de publicar.']}

def research(candidate,source_text=''):
    if not OPENAI_API_KEY:
        return _source_research(candidate,source_text)
    prompt=f'''Investiga y contrasta esta posible noticia exclusivamente en relación con La Línea de la Concepción. Busca fuentes públicas actuales, dando prioridad a fuentes oficiales y documentos. No redactes aún la noticia. Devuelve JSON con: facts (lista), context (lista), sources (lista de objetos name,url), caveats (lista).
TEMA: {candidate['title']}
URL INICIAL: {candidate.get('url','')}
TEXTO INICIAL: {source_text[:9000]}'''
    return json_from_text(ask_openai(SYSTEM,prompt,web=True))

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

def alternate_headlines(article):
    if not OPENAI_API_KEY:
        raise AIProviderError('Para generar titulares con ChatGPT falta OPENAI_API_KEY en Railway.')
    prompt=f'''Propón exactamente siete titulares periodísticos distintos para InfoLinense. Deben ser directos, claros, verificables, sin sensacionalismo ni exclamaciones y con utilidad para La Línea. No repitas el titular actual.
NOTICIA: {article.get('headline','')}
ENTRADILLA: {article.get('subtitle','')}
TEXTO: {(article.get('body') or '')[:2200]}
FUENTES: {article.get('sources_json') or ''}
Devuelve solo JSON válido con la clave headlines y una lista de exactamente siete textos.'''
    data=json_from_text(ask_openai(SYSTEM,prompt,web=False))
    values=data.get('headlines') if isinstance(data,dict) else None
    clean=[]
    for value in values or []:
        title=re.sub(r'\s+',' ',str(value)).strip().strip('"')
        if title and title.casefold()!=str(article.get('headline') or '').strip().casefold() and title.casefold() not in {x.casefold() for x in clean}:
            clean.append(title[:140])
    if len(clean)!=7:
        raise AIProviderError('ChatGPT no devolvió siete titulares diferentes. Vuelve a intentarlo.')
    return clean


def generate_carousel(article):
    if not OPENAI_API_KEY:
        raise AIProviderError('Para crear un carrusel con ChatGPT falta OPENAI_API_KEY en Railway.')
    prompt=f'''Analiza esta noticia para redes de InfoLinense. Crea un carrusel solo si permite explicar un proceso, varias claves, cifras o consecuencias mejor que una imagen única. Si no encaja, devuelve suitable=false, reason y slides=[].
Cuando encaje, devuelve suitable=true y entre 3 y 6 diapositivas. La primera presenta el tema; las siguientes explican hechos distintos; la última resume qué cambia o qué debe saber el vecino. Cada diapositiva debe incluir title (máximo 70 caracteres), text (máximo 220 caracteres) y photo_query (qué fotografía real buscar, sin inventar una foto ni sugerir que se genere con IA). Basa todo únicamente en los datos facilitados y las fuentes; indica en reason cualquier limitación. Devuelve JSON válido con suitable, reason y slides.
TITULAR: {article.get('headline','')}
ENTRADILLA: {article.get('subtitle','')}
CUERPO: {(article.get('body') or '')[:2200]}
SECCIÓN: {article.get('section','')}
FUENTES: {article.get('sources_json') or ''}
NOTAS DE CONTRASTE: {article.get('research_notes') or ''}'''
    data=json_from_text(ask_openai(SYSTEM,prompt,web=False))
    suitable=bool(data.get('suitable'))
    slides=data.get('slides') if isinstance(data.get('slides'),list) else []
    if suitable and not 3<=len(slides)<=6:
        raise AIProviderError('ChatGPT no devolvió entre tres y seis diapositivas válidas.')
    normalized=[]
    for slide in slides:
        normalized.append({
            'title':re.sub(r'\s+',' ',str(slide.get('title') or '')).strip()[:70],
            'text':re.sub(r'\s+',' ',str(slide.get('text') or '')).strip()[:220],
            'photo_query':re.sub(r'\s+',' ',str(slide.get('photo_query') or slide.get('photo_suggestion') or '')).strip()[:160],
            'photo_suggestion':re.sub(r'\s+',' ',str(slide.get('photo_query') or slide.get('photo_suggestion') or '')).strip()[:160],
        })
    if suitable and any(not s['title'] or not s['text'] for s in normalized):
        raise AIProviderError('ChatGPT devolvió una diapositiva sin titular o texto.')
    return {'suitable':suitable,'reason':str(data.get('reason') or '')[:500],'slides':normalized}


def discover_candidates():
    prompt='''Busca noticias, documentos, anuncios públicos y publicaciones públicas recientes que puedan afectar de forma concreta a La Línea de la Concepción.
Incluye fuentes locales y comarcales (prensa, Ayuntamiento, tablón de edictos, BOP/BOJA, licitaciones, redes públicas), y noticias nacionales o internacionales solo cuando puedas explicar una consecuencia verificable para vecinos de La Línea. Ejemplos de temas aplicables: vivienda y alquiler, empleo, coste de vida, ayudas, sanidad, educación, energía, transporte, clima o frontera.
No fuerces una relación local. Para cada resultado explica en local_angle qué cambia o por qué importa aquí; debe nombrar La Línea o un efecto local comprobable. Prioriza exclusivas, edictos, licitaciones, documentos oficiales y hechos nuevos. Evita duplicados y resultados donde “línea” no sea la ciudad.
Devuelve JSON válido con clave items y máximo 15 objetos con: title,url,source_name,excerpt,local_angle,official (boolean).'''
    return json_from_text(ask_openai(SYSTEM,prompt,web=True)).get('items',[])
