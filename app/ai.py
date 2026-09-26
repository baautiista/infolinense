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

SYSTEM='''Eres el motor editorial de InfoLinense, medio 100% centrado en La Línea de la Concepción. Redactas en español natural, periodístico y local. No copies notas de prensa. Prioriza utilidad, qué cambia, cómo afecta al ciudadano, fechas, importes, plazos y administración competente. Evita sensacionalismo, propaganda y titulares burocráticos. Si una afirmación no está sustentada, no la inventes. Devuelve JSON válido sin markdown.'''

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
    """Create a source-attributed, review-only brief without an external model."""
    title = BeautifulSoup(candidate.get('title') or '', 'html.parser').get_text(' ', strip=True).strip(' .')[:180]
    if not title:
        raise ValueError('La noticia no tiene titular')
    excerpt = BeautifulSoup(candidate.get('excerpt') or '', 'html.parser').get_text(' ', strip=True)
    text = BeautifulSoup(source_text or '', 'html.parser').get_text(' ', strip=True)
    source = (candidate.get('source_name') or urlparse(candidate.get('url') or '').netloc or 'la fuente original').strip()
    # The original page may include navigation and unrelated stories. Prefer the
    # source summary; include one short sentence from the page only if relevant.
    excerpt = re.sub(r'\s+', ' ', excerpt).strip()
    excerpt = re.sub(r'^(?:' + re.escape(title) + r')[\s:;.,-]*', '', excerpt, flags=re.I).strip()
    sentences = re.split(r'(?<=[.!?])\s+', text)
    title_terms = {w.lower() for w in re.findall(r'[\wáéíóúñü]{5,}', title)} - {'línea', 'concepción'}
    relevant = next((s for s in sentences if 35 <= len(s) <= 260 and len(title_terms.intersection(w.lower() for w in re.findall(r'[\wáéíóúñü]{5,}', s))) >= 2 and s.casefold() not in excerpt.casefold()), '')
    detail = excerpt[:320].rsplit(' ', 1)[0] if len(excerpt) > 320 else excerpt
    if not detail and relevant:
        detail = relevant[:320]
    if not detail:
        detail = 'La fuente original recoge esta información sobre La Línea de la Concepción.'
    detail = detail.strip(' .') + '.'
    # The brief attributes every factual sentence; no invented dates, figures or context.
    body = f'{title}.\n\nSegún {source}, {detail[0].lower() + detail[1:] if detail else "se ha publicado esta información."}'
    if relevant and relevant.casefold() not in detail.casefold():
        body += '\n\nLa fuente también señala: «' + relevant[:240].strip(' .') + '». '
    body += '\n\nConsulta la fuente original y verifica los datos antes de publicar.'
    low = title.casefold()
    groups = [('URBANISMO', ('obra', 'vivienda', 'urbanismo', 'licit', 'calle', 'plaza')), ('GIBRALTAR', ('gibraltar', 'frontera', 'verja')), ('DEPORTES', ('deporte', 'club', 'campeonato')), ('CULTURA', ('cultura', 'museo', 'teatro', 'festival')), ('AGENDA', ('agenda', 'concierto', 'fecha')), ('COMERCIO', ('comercio', 'mercado', 'hostelería'))]
    section = next((name for name, terms in groups if any(word in low for word in terms)), 'CIUDAD')
    return {'section': section, 'headline': title, 'subtitle': detail[:180], 'body': body[:2200], 'social_text': body[:2200], 'graphic_summary': detail[:180], 'ai_image_suggestion': ''}

def discover_candidates():
    prompt='''Busca noticias, documentos y anuncios públicos MUY RECIENTES que afecten directamente a La Línea de la Concepción. Prioriza urbanismo/obras, servicios públicos, Gibraltar o frontera cuando afecte a La Línea, agenda/cultura, comercio/aperturas, empleo/economía, contratos/licitaciones/presupuestos, patrimonio e incidencias ciudadanas. Prioriza fuentes oficiales, documentos y páginas originales. Evita cualquier resultado donde “línea” no sea la ciudad. Devuelve JSON con una clave items que sea lista de objetos: title,url,source_name,excerpt,official (boolean). Máximo 15 resultados, sin duplicados.'''
    return json_from_text(ask(SYSTEM,prompt,web=True)).get('items',[])
