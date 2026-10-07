import json, re, time, requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from .config import (OPENAI_API_KEY, OPENAI_MODEL, OPENAI_WEB_SEARCH,
                    ANTHROPIC_API_KEY, ANTHROPIC_MODEL, ANTHROPIC_WORKSPACE_ID,
                    ANTHROPIC_WEB_SEARCH, AI_PROVIDER, AI_FALLBACK,
                    AI_ALLOW_PAID, GEMINI_API_KEY, GEMINI_MODEL, GEMINI_FALLBACK_MODELS, GEMINI_MIN_INTERVAL)
import threading as _threading

PROVIDER_NAMES = {'anthropic': 'Claude (Anthropic)', 'openai': 'ChatGPT (OpenAI)', 'gemini': 'Gemini (Google, gratis)'}
PAID = ('openai', 'anthropic')


def _configured_provider():
    if not AI_ALLOW_PAID:
        # Modo sin coste: solo la IA gratuita de Google; si no hay clave, borrador a partir de la fuente.
        return 'gemini' if GEMINI_API_KEY else ''
    if AI_PROVIDER in ('gemini', 'google'):
        return 'gemini' if GEMINI_API_KEY else ''
    if AI_PROVIDER in ('anthropic', 'claude'):
        return 'anthropic' if ANTHROPIC_API_KEY else ''
    if AI_PROVIDER in ('openai', 'gpt'):
        return 'openai' if OPENAI_API_KEY else ''
    # En modo automático redacta ChatGPT (OpenAI); Claude queda como respaldo.
    if OPENAI_API_KEY: return 'openai'
    if ANTHROPIC_API_KEY: return 'anthropic'
    return ''


ACTIVE_PROVIDER = _configured_provider()
AI_ENABLED = bool(ACTIVE_PROVIDER)


def provider_chain():
    """Providers to try, in order. The second one is only a fallback."""
    chain = [ACTIVE_PROVIDER] if ACTIVE_PROVIDER else []
    if AI_FALLBACK and AI_ALLOW_PAID:
        for name, key in (('openai', OPENAI_API_KEY), ('anthropic', ANTHROPIC_API_KEY), ('gemini', GEMINI_API_KEY)):
            if key and name not in chain:
                chain.append(name)
    return [p for p in chain if AI_ALLOW_PAID or p not in PAID]  # nunca de pago sin permiso


def web_enabled(provider):
    return bool(ANTHROPIC_WEB_SEARCH) if provider == 'anthropic' else bool(OPENAI_WEB_SEARCH) if provider == 'openai' else False


class AIProviderError(RuntimeError):
    """An AI provider rejected a request; safe to surface to the authenticated editor.

    kind: auth | quota | billing | rate_limit | overloaded | bad_request | network | empty | config | error
    """

    def __init__(self, message, provider='', status=None, code='', kind='error', retry_after=None, raw=''):
        super().__init__(message)
        self.provider = provider
        self.status = status
        self.code = code
        self.kind = kind
        self.retry_after = retry_after
        self.raw = raw
        self.fallback_errors = []

    def as_dict(self):
        out = {'message': str(self), 'provider': self.provider,
               'provider_name': PROVIDER_NAMES.get(self.provider, self.provider or ''),
               'http_status': self.status, 'code': self.code, 'kind': self.kind,
               'retry_after_seconds': self.retry_after, 'provider_message': self.raw,
               'retryable': self.kind in ('rate_limit', 'overloaded', 'network', 'empty', 'error')}
        if self.fallback_errors:
            out['fallback'] = [e.as_dict() for e in self.fallback_errors]
        return out


def _hide_keys(text):
    text = re.sub(r'sk-ant-[A-Za-z0-9_-]+', '[clave oculta]', str(text or ''))
    return re.sub(r'sk-[A-Za-z0-9_-]+', '[clave oculta]', text)[:300]


def _retry_after(response):
    try:
        value = response.headers.get('retry-after')
        return int(float(value)) if value else None
    except (TypeError, ValueError):
        return None


def _openai_error(response):
    try:
        api_error = (response.json() or {}).get('error') or {}
    except Exception:
        api_error = {}
    message = _hide_keys(api_error.get('message') if isinstance(api_error, dict) else '')
    code = str((api_error.get('code') if isinstance(api_error, dict) else '') or '').lower()
    etype = str((api_error.get('type') if isinstance(api_error, dict) else '') or '').lower()
    status = response.status_code
    tag = code or etype
    quota_codes = {'insufficient_quota', 'credit_balance_exhausted', 'billing_hard_limit_reached',
                   'organization_spend_limit_exceeded', 'project_spend_limit_exceeded',
                   'organization_usage_limit_exceeded', 'usage_limit_exceeded'}
    if status in (401, 403):
        kind, text = 'auth', 'OpenAI no acepta la clave API (OPENAI_API_KEY). Revisa que sea válida y esté activa.'
    elif status == 429 and (code in quota_codes or etype == 'insufficient_quota'):
        kind, text = 'quota', 'OpenAI dice que la cuenta API no tiene saldo o ha llegado a su límite de gasto (Billing/Limits en platform.openai.com).'
    elif status == 429:
        kind, text = 'rate_limit', 'OpenAI está limitando las peticiones por exceso de velocidad. No es un problema de saldo: espera un minuto y reintenta.'
    elif status in (500, 502, 503, 504):
        kind, text = 'overloaded', 'Los servidores de OpenAI están fallando o saturados. Reintenta en unos minutos.'
    elif status == 404 or 'model' in message.lower():
        kind, text = 'bad_request', f'OpenAI no reconoce el modelo configurado ({OPENAI_MODEL}). Revisa OPENAI_MODEL en Railway.'
    else:
        kind, text = 'bad_request', f'OpenAI rechazó la solicitud (HTTP {status}).'
    return AIProviderError(text, 'openai', status, tag, kind, _retry_after(response), message)


def _anthropic_error(response):
    try:
        payload = response.json()
    except Exception:
        payload = {}
    error = payload.get('error', {}) if isinstance(payload, dict) else {}
    message = _hide_keys(error.get('message', '') if isinstance(error, dict) else '')
    etype = str(error.get('type', '') if isinstance(error, dict) else '')
    lower = message.lower()
    status = response.status_code
    if 'credit balance' in lower or status == 402:
        kind, text = 'billing', 'La cuenta API de Anthropic no tiene saldo suficiente. Añade créditos en console.anthropic.com → Billing.'
    elif any(t in lower for t in ('spend limit', 'spending limit', 'usage limit', 'budget limit', 'monthly limit')):
        kind, text = 'quota', 'Anthropic ha bloqueado la petición por el límite de gasto de la cuenta. Revisa Limits en console.anthropic.com.'
    elif 'workspace' in lower and ('required' in lower or ' id' in lower):
        kind, text = 'config', 'Anthropic pide el ID del espacio de trabajo. Añade ANTHROPIC_WORKSPACE_ID en Railway.'
    elif status in (401, 403):
        kind, text = 'auth', 'Anthropic no acepta la clave API (ANTHROPIC_API_KEY). Comprueba que esté activa en console.anthropic.com.'
    elif status == 429:
        kind, text = 'rate_limit', 'Anthropic está limitando las peticiones por exceso de velocidad. Espera un minuto y reintenta.'
    elif status in (500, 502, 503, 504, 529):
        kind, text = 'overloaded', 'Los servidores de Claude están saturados. Reintenta en unos minutos.'
    elif status == 404 or 'model' in lower:
        kind, text = 'bad_request', f'Anthropic no reconoce el modelo configurado ({ANTHROPIC_MODEL}). Revisa ANTHROPIC_MODEL en Railway.'
    else:
        kind, text = 'bad_request', f'Claude rechazó la solicitud (HTTP {status}).'
    return AIProviderError(text, 'anthropic', status, etype, kind, _retry_after(response), message)


def _extract_text(data):
    if isinstance(data, dict):
        if data.get('output_text'): return data['output_text']
        blocks = data.get('content', [])
        out = [c['text'] for c in blocks if isinstance(c, dict) and c.get('type') == 'text' and c.get('text')] if isinstance(blocks, list) else []
        if out: return '\n'.join(out)
        out = []
        for item in data.get('output', []) or []:
            if not isinstance(item, dict): continue
            for c in item.get('content', []) or []:
                if isinstance(c, dict) and c.get('text'): out.append(c['text'])
        return '\n'.join(out)
    return ''


def ask_openai(instructions, user_text, web=False, max_tokens=4000):
    if not OPENAI_API_KEY:
        raise AIProviderError('Falta OPENAI_API_KEY en Railway.', 'openai', kind='config')
    payload = {'model': OPENAI_MODEL, 'instructions': instructions, 'input': user_text,
               'max_output_tokens': max_tokens, 'store': False}
    if web:
        payload['tools'] = [{'type': 'web_search'}]
    try:
        response = requests.post('https://api.openai.com/v1/responses',
                                 headers={'Authorization': f'Bearer {OPENAI_API_KEY}', 'Content-Type': 'application/json'},
                                 json=payload, timeout=150)
    except requests.RequestException as exc:
        raise AIProviderError('No se pudo contactar con la API de OpenAI (red o tiempo de espera).', 'openai', kind='network') from exc
    if not response.ok:
        raise _openai_error(response)
    answer = _extract_text(response.json())
    if not answer:
        raise AIProviderError('ChatGPT no devolvió texto. Reintenta.', 'openai', 200, kind='empty')
    return answer


_gemini_lock = _threading.Lock()
_gemini_last = [0.0]
_gemini_bad_models = set()


def _gemini_retry_seconds(err):
    for d in (err.get('details') or []) if isinstance(err, dict) else []:
        delay = str((d or {}).get('retryDelay') or '')
        m = re.match(r'(\d+(?:\.\d+)?)s', delay)
        if m:
            return float(m.group(1))
    return None


def ask_gemini(instructions, user_text, web=False, max_tokens=4000):
    """Google Gemini (nivel gratuito). Una petición cada GEMINI_MIN_INTERVAL segundos como máximo;
    si un modelo no tiene cupo gratuito se prueba el siguiente; si es un límite por minuto, espera y repite."""
    if not GEMINI_API_KEY:
        raise AIProviderError('Falta GEMINI_API_KEY en Railway.', 'gemini', kind='config')
    payload = {'systemInstruction': {'parts': [{'text': instructions}]},
               'contents': [{'role': 'user', 'parts': [{'text': user_text}]}],
               'generationConfig': {'maxOutputTokens': max(2048, max_tokens * 2), 'temperature': 0.6}}
    models = [m for m in dict.fromkeys([GEMINI_MODEL] + list(GEMINI_FALLBACK_MODELS)) if m not in _gemini_bad_models] or [GEMINI_MODEL]
    last_error = None
    with _gemini_lock:  # una petición cada vez, espaciadas
        for model in models:
            for attempt in range(2):
                wait = GEMINI_MIN_INTERVAL - (time.time() - _gemini_last[0])
                if wait > 0:
                    time.sleep(wait)
                url = 'https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent' % model
                try:
                    r = requests.post(url, params={'key': GEMINI_API_KEY}, json=payload, timeout=150)
                except requests.RequestException as exc:
                    raise AIProviderError('No se pudo contactar con Gemini (red o tiempo de espera).', 'gemini', kind='network') from exc
                finally:
                    _gemini_last[0] = time.time()
                if r.ok:
                    data = r.json()
                    parts = ((data.get('candidates') or [{}])[0].get('content') or {}).get('parts') or []
                    text = '\n'.join(p.get('text', '') for p in parts if isinstance(p, dict) and not p.get('thought'))
                    if not text.strip():
                        raise AIProviderError('Gemini no devolvió texto. Reintenta.', 'gemini', 200, kind='empty')
                    return text
                try: err = (r.json() or {}).get('error') or {}
                except Exception: err = {}
                msg = _hide_keys(err.get('message', '')) if isinstance(err, dict) else ''
                status = r.status_code
                if status == 404 or (status == 429 and re.search(r'limit:\s*0\b', msg)):
                    _gemini_bad_models.add(model)  # sin cupo gratuito o inexistente: siguiente modelo
                    last_error = (status, err, msg, model)
                    break
                if status == 429:
                    delay = _gemini_retry_seconds(err)
                    per_day = bool(re.search(r'per\s*day|PerDay|daily', msg, re.I))
                    if attempt == 0 and not per_day and (delay is None or delay <= 65):
                        time.sleep(delay or 30)  # límite por minuto: esperar y repetir
                        continue
                    last_error = (status, err, msg, model)
                    break  # probar otro modelo (cada uno tiene su propio cupo)
                if status in (401, 403) or 'api key' in msg.lower():
                    raise AIProviderError('Google no acepta la clave GEMINI_API_KEY. Créala de nuevo en aistudio.google.com.', 'gemini', status, '', 'auth', None, msg)
                if status >= 500:
                    last_error = (status, err, msg, model)
                    if attempt == 0:
                        time.sleep(10)
                        continue
                    break
                raise AIProviderError(f'Gemini rechazó la solicitud (HTTP {status}).', 'gemini', status, '', 'bad_request', None, msg)
    status, err, msg, model = last_error or (429, {}, '', GEMINI_MODEL)
    if status == 404:
        raise AIProviderError('Google no reconoce los modelos de Gemini configurados. Revisa GEMINI_MODEL en Railway.', 'gemini', 404, '', 'bad_request', None, msg)
    if status >= 500:
        raise AIProviderError('Los servidores de Gemini están saturados. Reintenta en unos minutos.', 'gemini', status, '', 'overloaded', None, msg)
    daily = bool(re.search(r'per\s*day|PerDay|daily', msg, re.I))
    text = ('Se ha gastado el cupo gratuito de Gemini de hoy. Las noticias pendientes se redactarán solas cuando se renueve (mañana).'
            if daily else 'Gemini pide esperar un poco (límite gratuito por minuto). Se reintentará solo en unos minutos.')
    raise AIProviderError(text, 'gemini', 429, 'daily' if daily else 'per_minute', 'rate_limit', _gemini_retry_seconds(err), msg)


def ask_anthropic(instructions, user_text, web=False, max_tokens=4000):
    if not ANTHROPIC_API_KEY:
        raise AIProviderError('Falta ANTHROPIC_API_KEY en Railway.', 'anthropic', kind='config')
    payload = {'model': ANTHROPIC_MODEL, 'max_tokens': max_tokens, 'system': instructions,
               'messages': [{'role': 'user', 'content': user_text}]}
    if web:
        payload['tools'] = [{'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 4,
                             'user_location': {'type': 'approximate', 'city': 'La Línea de la Concepción',
                                               'region': 'Andalucía', 'country': 'ES', 'timezone': 'Europe/Madrid'}}]
    headers = {'x-api-key': ANTHROPIC_API_KEY, 'anthropic-version': '2023-06-01', 'Content-Type': 'application/json'}
    if ANTHROPIC_WORKSPACE_ID:
        headers['anthropic-workspace-id'] = ANTHROPIC_WORKSPACE_ID
    collected = []
    for _ in range(4):
        try:
            r = requests.post('https://api.anthropic.com/v1/messages', headers=headers, json=payload, timeout=150)
        except requests.RequestException as exc:
            raise AIProviderError('No se pudo contactar con la API de Claude (red o tiempo de espera).', 'anthropic', kind='network') from exc
        if not r.ok:
            raise _anthropic_error(r)
        data = r.json()
        text = _extract_text(data)
        if text: collected.append(text)
        if data.get('stop_reason') != 'pause_turn':
            if not collected:
                raise AIProviderError('Claude no devolvió texto. Reintenta.', 'anthropic', 200, kind='empty')
            return '\n'.join(collected)
        payload['messages'].append({'role': 'assistant', 'content': data.get('content', [])})
    raise AIProviderError('Claude dejó la respuesta incompleta. Reintenta.', 'anthropic', 200, kind='empty')


def ask_ai(instructions, user_text, web=False, web_required=False, max_tokens=4000):
    """Call the active provider; on any provider error, try the other configured one.

    web=True uses web search only where it is enabled in Railway. With web_required,
    providers without web search are skipped.
    Returns (text, provider, used_web).
    """
    chain = provider_chain()
    if not chain:
        raise AIProviderError('No hay ninguna clave de IA configurada en Railway (ANTHROPIC_API_KEY u OPENAI_API_KEY).', kind='config')
    first_error = None
    for provider in chain:
        use_web = bool(web and web_enabled(provider))
        if web_required and not use_web:
            continue
        try:
            fn = {'anthropic': ask_anthropic, 'openai': ask_openai, 'gemini': ask_gemini}[provider]
            return fn(instructions, user_text, web=use_web, max_tokens=max_tokens), provider, use_web
        except AIProviderError as exc:
            if first_error is None:
                first_error = exc
            else:
                first_error.fallback_errors.append(exc)
    if first_error is None:
        raise AIProviderError('Ningún proveedor de IA tiene activada la búsqueda web (ANTHROPIC_WEB_SEARCH / OPENAI_WEB_SEARCH).', kind='config')
    raise first_error


def ask(instructions, user_text, web=False):
    """Backwards-compatible helper."""
    return ask_ai(instructions, user_text, web=web)[0]


def check_providers():
    """Make a tiny real request to each configured provider and report what it returns."""
    out = []
    for provider in ('gemini', 'openai', 'anthropic'):
        key = {'anthropic': ANTHROPIC_API_KEY, 'openai': OPENAI_API_KEY, 'gemini': GEMINI_API_KEY}[provider]
        item = {'provider': provider, 'provider_name': PROVIDER_NAMES[provider], 'configured': bool(key),
                'model': {'anthropic': ANTHROPIC_MODEL, 'openai': OPENAI_MODEL, 'gemini': GEMINI_MODEL}[provider],
                'active': provider == ACTIVE_PROVIDER, 'web_search': web_enabled(provider),
                'paid': provider in PAID, 'blocked': provider in PAID and not AI_ALLOW_PAID}
        if key and item['blocked']:
            item.update({'ok': None, 'message': 'Desactivada: es de pago (modo sin coste)'})
        elif key:
            started = time.time()
            try:
                fn = {'anthropic': ask_anthropic, 'openai': ask_openai, 'gemini': ask_gemini}[provider]
                fn('Responde solo con la palabra OK.', 'Prueba de conexión', max_tokens=16 if provider == 'anthropic' else 32)
                item.update({'ok': True, 'message': 'Responde correctamente'})
            except AIProviderError as exc:
                item.update({'ok': False, **{k: v for k, v in exc.as_dict().items() if k != 'provider'}})
            item['seconds'] = round(time.time() - started, 1)
        out.append(item)
    return {'active_provider': ACTIVE_PROVIDER or None, 'fallback_enabled': AI_FALLBACK, 'paid_allowed': AI_ALLOW_PAID,
            'order': provider_chain(), 'providers': out}


def json_from_text(t):
    t = (t or '').strip()
    t = re.sub(r'^```(?:json)?|```$', '', t, flags=re.M).strip()
    try:
        return json.loads(t)
    except Exception:
        m = re.search(r'\{.*\}', t, re.S)
        if not m:
            raise AIProviderError('La IA no devolvió datos en el formato esperado. Reintenta.', kind='empty')
        try:
            return json.loads(m.group(0))
        except Exception as exc:
            raise AIProviderError('La IA devolvió una respuesta incompleta. Reintenta.', kind='empty') from exc


def ask_json(instructions, prompt, web=False, web_required=False, max_tokens=4000):
    text, provider, used_web = ask_ai(instructions, prompt, web=web, web_required=web_required, max_tokens=max_tokens)
    return json_from_text(text), provider, used_web


SYSTEM = '''Eres el redactor jefe de InfoLinense, medio digital de La Línea de la Concepción (Cádiz). Escribes en español claro, cercano y periodístico para vecinos de La Línea.
Reglas que nunca rompes:
- Solo afirmas lo que consta en las fuentes facilitadas. No inventas datos, fechas, cifras, nombres, citas ni declaraciones. Lo que no se sabe, simplemente no se menciona.
- Atribuyes cada hecho a su fuente (Ayuntamiento, BOP, el medio, etc.).
- No copias frases ni el tono de la nota de prensa: nada de autobombo institucional, adjetivos de gabinete ni fórmulas como «apuesta decidida» o «en aras de».
- Sin emojis, sin exclamaciones, sin sensacionalismo ni muletillas de IA.
- No empieces el cuerpo con «La Línea de la Concepción».
- Devuelves únicamente JSON válido, sin markdown.'''

from . import layout
SECTIONS = '; '.join('%s (o más concreta: %s)' % (f, ', '.join(layout.SUBSECTIONS.get(f, []))) for f in layout.FAMILY_NAMES)
SECTION_GUIDE = ('OBRAS = obras, urbanismo, infraestructuras, movilidad, tráfico, aparcamientos; CIUDAD = Ayuntamiento, servicios municipales, barrios; '
                 'GIBRALTAR = Gibraltar, frontera, relaciones transfronterizas, Campo de Gibraltar; SUCESOS = seguridad, policía, bomberos, emergencias; '
                 'CULTURA = carnaval, cofradías, música, teatro, exposiciones, ocio; DEPORTES; COMERCIO = empresas, hostelería, turismo, negocios; '
                 'POLÍTICA = elecciones, plenos, partidos, administración; SOCIEDAD = economía, empleo, vivienda, educación, sanidad, asociaciones; '
                 'PATRIMONIO = historia, memoria, efemérides, arqueología; AGENDA = planes, eventos, qué hacer, fin de semana; '
                 'MEDIO AMBIENTE = playas, limpieza, parques, naturaleza, residuos')


def _source_research(candidate,source_text=''):
    excerpt = BeautifulSoup(candidate.get('excerpt') or '', 'html.parser').get_text(' ', strip=True)
    excerpt = re.sub(r'\s+', ' ', excerpt)[:350]
    facts = [excerpt] if excerpt and excerpt.casefold() != (candidate.get('title') or '').casefold() else []
    return {'facts': facts, 'context': [],
            'sources': [{'name': candidate.get('source_name') or urlparse(candidate.get('url') or '').netloc or 'Fuente original', 'url': candidate.get('url') or ''}],
            'caveats': ['Extracto de la fuente original, sin contraste independiente. Comprobar fechas, cifras y contexto antes de publicar.']}

def research(candidate, source_text=''):
    """Gather verifiable facts. Uses web search only where enabled; otherwise analyses the source text."""
    if not AI_ENABLED or ACTIVE_PROVIDER == 'gemini':
        # Plan gratuito: no se gasta una petición en investigar; se redacta directamente con el texto de la fuente.
        return _source_research(candidate, source_text)
    prompt = f'''Reúne los hechos de una posible noticia para vecinos de La Línea de la Concepción. No redactes todavía la noticia.
Si puedes buscar en internet, completa el tema con información pública actual (Ayuntamiento, BOP, BOJA, BOE, contratación, prensa) y antecedentes locales que ayuden a entenderlo.
Devuelve JSON con estas claves:
- facts: lista de hechos concretos (qué, dónde, cuándo, cuánto cuesta, cuánto tarda, empresa, a quién afecta, qué paso administrativo es). Solo lo que se sabe; no anotes lo que falta.
- context: lista de antecedentes y contexto local útil (proyectos anteriores, calles, barrios, obras cercanas, problemas históricos).
- sources: lista de objetos {{name, url}} consultados; incluye siempre la fuente original.
TEMA: {candidate.get('title', '')}
FUENTE ORIGINAL: {candidate.get('source_name', '')} · {candidate.get('url', '')}
FECHA DE LA FUENTE: {candidate.get('published_at') or 'no consta'}
ÁNGULO LOCAL DETECTADO: {candidate.get('local_angle') or ''}
EXTRACTO: {candidate.get('excerpt', '')[:1500]}
TEXTO FUENTE: {(source_text or '')[:10000]}'''
    data, provider, used_web = ask_json(SYSTEM, prompt, web=True, max_tokens=4000)
    if not isinstance(data, dict):
        data = {}
    sources = [s for s in (data.get('sources') or []) if isinstance(s, dict) and s.get('url')]
    if candidate.get('url') and not any(s.get('url') == candidate.get('url') for s in sources):
        sources.insert(0, {'name': candidate.get('source_name') or 'Fuente original', 'url': candidate.get('url')})
    data['sources'] = sources
    data['provider'] = provider
    data['web_search'] = used_web
    return data


def _facts_for_draft(research_json):
    """Solo hechos y contexto: nada de avisos ni «datos que faltan», para que no acaben en el texto."""
    try:
        data = json.loads(research_json) if isinstance(research_json, str) else (research_json or {})
    except Exception:
        return str(research_json or '')[:8000]
    if not isinstance(data, dict):
        return ''
    keep = {k: data.get(k) for k in ('facts', 'context', 'what', 'where', 'when', 'who_affected') if data.get(k)}
    text = json.dumps(keep, ensure_ascii=False)
    return re.sub(r'[^"]*(no consta|se desconoce|no se especifica|no se indica)[^"]*', '', text, flags=re.I)


# Frases de «lo que no se sabe» o de la fuente que no deben aparecer en una noticia publicada.
_META = re.compile(r'(no consta|no constan|se desconoce|desconocemos|no se ha (podido )?(encontrar|confirmar|precisar|detallar|especificar|concretar)|'
                   r'no se especific|no se detall|no se indic|no se precis|no ha trascendido|no han trascendido|sin que (se|conste)|'
                   r'(la|las) fuentes? (consultada|disponible|original)|el documento no|la documentación|según la documentación|'
                   r'falta(n)? (por )?confirmar|queda(n)? por confirmar|está por confirmar|no se ha hecho público|no se ha informado|'
                   r'la nota (de prensa )?no|la información disponible|no se aporta|no aporta(n)? (más )?datos|sin más detalles)', re.I)


def clean_meta(text):
    """Quita frases sobre datos que faltan o sobre las fuentes («no consta», «se desconoce dónde…»)."""
    out = []
    for para in re.split(r'\n\s*\n', str(text or '')):
        sentences = re.split(r'(?<=[.!?])\s+', para.strip())
        kept = [x for x in sentences if x and not _META.search(x)]
        if kept:
            out.append(' '.join(kept))
    return '\n\n'.join(out).strip()


def _trim_body(text, limit=2200):
    text = re.sub(r'[ \t]+', ' ', str(text or '')).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind('. '), cut.rfind('.\n'), cut.rfind('.'))
    return cut[:end + 1] if end > limit * 0.7 else cut.rsplit(' ', 1)[0]


def _clean_headlines(values, current=''):
    clean = []
    for value in values or []:
        title = re.sub(r'\s+', ' ', str(value)).strip().strip('"«»').rstrip('.')
        if title and title.casefold() != str(current or '').strip().casefold() and title.casefold() not in {x.casefold() for x in clean}:
            clean.append(title[:140])
    return clean


from pathlib import Path as _Path
STYLE_FILE = _Path(__file__).with_name('estilo_redaccion.md')


def style_guide():
    """Guía de estilo de InfoLinense (app/estilo_redaccion.md). Se lee en cada redacción para poder editarla."""
    try:
        return STYLE_FILE.read_text(encoding='utf-8')
    except OSError:
        return SYSTEM


def draft(candidate, source_text='', research='', quick=False):
    if not AI_ENABLED:
        return free_draft(candidate, source_text)
    from . import brands
    b = brands.settings(candidate.get('brand'))
    other = b['slug'] != brands.DEFAULT
    sections = ' | '.join(b['sections']) if other else SECTIONS
    guide = 'elige la que mejor encaje' if other else SECTION_GUIDE
    if other and b.get('page_mode') == 'section':
        guide = ('si la noticia es de una hermandad concreta, su nombre tal cual; si trata de varias o de la Semana Santa en general, '
                 f"«{b.get('free_title_section') or 'General'}»; pregones, pregoneros, carteles, salidas extraordinarias o "
                 'aniversarios, «Ocasiones especiales»')
    label_key = (f"section_label (solo si section es «{b['free_title_section']}»: la etiqueta de la imagen es el nombre de la hermandad, "
                 "banda u organización protagonista, corto y tal cual se conoce, p. ej. «Banda Santa Bárbara», «Consejo de Hermandades», "
                 f"«Agrupación Parroquial…»; si no hay una protagonista clara, «{brands.holy_week_label()}»),\n") if other and b.get('free_title_section') else ''
    medium = (f"MEDIO: escribes para {b['name']}, {b['about']}. Mismo rigor y estilo que InfoLinense, con el vocabulario propio "
              f"de ese mundo (sin explicar lo que su público ya sabe).\n") if other else ''
    if other and b.get('headline_lines'):
        n, c = int(b['headline_lines']), int(b['line_chars'] or 30)
        head_rule = (f'directo, cuenta la noticia y ocupa {n} líneas en la imagen: entre {c * (n - 1) + 5} y {c * n} caracteres '
                     f'(nunca más de {c * n}), porque va tal cual en la imagen')
    else:
        head_rule = ('directo, cuenta la noticia con un dato concreto; va tal cual en la imagen y ocupa 3 o 4 líneas: '
                     'entre 52 y 80 caracteres (si se queda corto, añade el dato clave: cifra, fecha, lugar o a quién afecta; '
                     'si es largo, no pasa nada: la imagen usa letra algo más pequeña)')
    prompt = f'''{medium}Redacta la noticia siguiendo al pie de la letra la guía de estilo de InfoLinense.
Usa SOLO la información de las fuentes de abajo, pero aprovecha TODO el TEXTO FUENTE, no solo el titular: incorpora todos los datos útiles que contiene (cifras, fechas, plazos, lugares, nombres de calles y barrios, empresas, requisitos, antecedentes, declaraciones relevantes).
Extensión del TEXTO: entre 1.800 y 2.200 caracteres con espacios. Solo si el texto fuente es muy corto (menos de 600 caracteres) puede quedar más breve, sin inventar ni rellenar.
Párrafos de 2 a 4 frases separados por una línea en blanco.
PROHIBIDO en titular, subtítulo y texto: hablar de lo que no se sabe o falta («no consta», «se desconoce», «no se ha encontrado», «no se especifica», «falta confirmar»), hablar de la fuente o del documento («según la documentación», «la nota no detalla») o pedir comprobaciones. Si un dato no está, no lo menciones y cuenta la noticia con lo que sí se sabe. Atribuye solo cuando lo haría un periodista («según el Ayuntamiento», «recoge el BOP»).
Céntrate en los hechos y en lo que cambia para la ciudad, no en tecnicismos ni en las fuentes.
ENFOQUE LA LÍNEA: si la noticia es comarcal (Campo de Gibraltar), de Gibraltar o de varios pueblos, el titular y la entradilla se centran en lo que toca a La Línea (su agrupación, sus vecinos, su calle, su dinero…) y lo demás se cuenta después en el texto. Ejemplo: «Cinco agrupaciones del Campo cantarán en el Falla» → «La comparsa linense X cantará en el Falla» y luego las demás.
{'Noticia de menor peso: pieza corta.' if quick else ''}

CANDIDATA: {candidate.get('title', '')}
FUENTE: {candidate.get('source_name', '')} · {candidate.get('url', '')}
FECHA DE LA FUENTE: {candidate.get('published_at') or 'no consta'}
EXTRACTO: {candidate.get('excerpt', '')[:1500]}
TEXTO FUENTE (léelo entero): {(source_text or '')[:12000]}
HECHOS Y CONTEXTO: {_facts_for_draft(research)[:8000]}

Devuelve la entrega (SECCIÓN, TITULAR, SUBTÍTULO, TEXTO) como JSON válido, sin markdown, con exactamente estas claves:
focus (ENFOQUE PRINCIPAL: la verdadera noticia en una frase),
section (exactamente una de: {sections}. Guía: {guide}. {'' if other else 'Usa la etiqueta más concreta que encaje (CONCIERTOS mejor que CULTURA, PLAYAS mejor que MEDIO AMBIENTE) y evita CIUDAD si hay otra mejor.'}),
{label_key}
headline (TITULAR: {head_rule}),
subtitle (SUBTÍTULO o ENTRADILLA: aporta información nueva, nunca repite el titular, máximo 160 caracteres porque va tal cual en la imagen),
body (TEXTO: la noticia completa, entre 1.800 y 2.200 caracteres, en 5 a 8 párrafos),
headline_options (lista de EXACTAMENTE 7 titulares alternativos con los mismos criterios, distintos entre sí y del titular principal),
carousel_suitable (true solo si la noticia explica varios pasos, cifras, requisitos o consecuencias que se entienden mejor en 3-6 diapositivas),
carousel_reason (motivo breve),
ai_image_suggestion (qué foto real buscar; no se genera ninguna imagen),
photo_query (búsqueda de imágenes en internet de 3 a 6 palabras que describa la escena concreta y el lugar, p. ej. «playa Poniente La Línea alga asiática» o «calle Real La Línea obras»).'''
    data, provider, _ = ask_json(style_guide(), prompt, max_tokens=5000)
    if not isinstance(data, dict) or not data.get('headline') or not data.get('body'):
        raise AIProviderError('La IA devolvió un borrador incompleto. Reintenta.', provider, kind='empty')
    for key in ('headline', 'subtitle', 'body'):
        data[key] = clean_meta(data.get(key)) or data.get(key) or ''
    data['body'] = _trim_body(data.get('body'))
    data['missing_data'] = []
    data['social_text'] = data['body']
    data['headline_options'] = _clean_headlines(data.get('headline_options'), data.get('headline'))[:7]
    if len(data['headline_options']) != 7 and provider != 'gemini':  # en el plan gratis no se gasta otra petición
        try:
            data['headline_options'] = alternate_headlines(data)
        except AIProviderError:
            pass
    if other:
        src = brands.find_section(b['slug'], candidate.get('source_name') or '') or brands.find_section(b['slug'], candidate.get('outlet') or '')
        free = b.get('free_title_section') or ''
        sec = src or brands.find_section(b['slug'], data.get('section'))  # página de la hermandad: su sección, siempre
        if not src and sec in ('', free):  # la IA no la reconoció: se busca la hermandad en el titular y el texto
            sec = brands.detect_section(b['slug'], (data.get('headline') or '') + ' ' + (data.get('body') or '')[:600]) or sec
        data['section'] = sec or free or (b['sections'][0] if b['sections'] else 'Noticias')
        label = str(data.get('section_label') or '').strip()[:40]
        data['section_label'] = (label or brands.holy_week_label()) if data['section'] == free else ''
    else:
        data['section'] = layout.normalize_section(data.get('section'), data.get('headline', '') + ' ' + data.get('body', '')[:400])
    # La imagen usa exactamente el titular y la entradilla de la noticia (no textos aparte).
    data['image_headline'] = ''
    data['graphic_summary'] = data.get('subtitle') or ''
    data['provider'] = provider
    return data


def photo_query(article):
    """Búsqueda de imágenes descriptiva para una noticia ya redactada."""
    prompt = (f"Escribe una búsqueda de imágenes en internet de 3 a 6 palabras para ilustrar esta noticia de La Línea de la Concepción. "
              f"Describe la escena concreta y el lugar (p. ej. «playa Poniente La Línea alga asiática»). Responde solo JSON {{\"q\": \"...\"}}.\n"
              f"TITULAR: {article.get('headline', '')}\nENTRADILLA: {article.get('subtitle', '')}")
    data, _, _ = ask_json(SYSTEM, prompt, max_tokens=200)
    return re.sub(r'\s+', ' ', str((data or {}).get('q') or '')).strip()[:120]


_DIAS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']
_MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']


def _fecha(d):
    return '%s %s de %s de %s' % (_DIAS[d.weekday()], d.day, _MESES[d.month - 1], d.year)


TIME_WORDS = re.compile(r'\b(hoy|mañana|ayer|anoche|anteayer|pasado mañana|esta (mañana|tarde|noche|semana)|este (lunes|martes|miércoles|'
                        r'jueves|viernes|sábado|domingo|fin de semana|mes)|el próximo|la próxima|el pasado|la pasada|'
                        r'lunes|martes|miércoles|jueves|viernes|sábado|domingo)\b', re.I)


def retime(article, old_date, new_date):
    """Ajusta las referencias de tiempo (hoy, mañana, ayer, este viernes…) a la nueva fecha de publicación.
    Devuelve {'headline','subtitle','body','changed'}; si no hay nada que ajustar, no gasta IA."""
    fields = {k: article.get(k) or '' for k in ('headline', 'subtitle', 'body')}
    if old_date == new_date or not any(TIME_WORDS.search(v) for v in fields.values()):
        return dict(fields, changed=False)
    if not AI_ENABLED:
        raise AIProviderError('Sin IA no se pueden ajustar las fechas del texto: revisa «hoy», «mañana»… a mano.', kind='config')
    prompt = f'''Este texto se escribió para publicarse el {_fecha(old_date)}. Ahora se publicará el {_fecha(new_date)}.
Ajusta SOLO las referencias de tiempo relativas para que sean correctas leídas el {_fecha(new_date)}: «hoy», «mañana», «ayer», «esta tarde»,
«este viernes», «el próximo lunes», «la semana que viene», tiempos verbales que dependan de ello («se celebra» → «se celebró» si ya habrá pasado), etc.
Si una referencia relativa queda confusa, cámbiala por el día de la semana o la fecha («el viernes 9»).
No cambies nada más: ni datos, ni estilo, ni orden, ni longitud (el titular no puede pasar de {max(80, len(fields['headline']) + 5)} caracteres).
Devuelve JSON con exactamente estas claves: headline, subtitle, body.
TITULAR: {fields['headline']}
ENTRADILLA: {fields['subtitle']}
TEXTO: {fields['body'][:4000]}'''
    data, _, _ = ask_json(SYSTEM, prompt, max_tokens=4000)
    if not isinstance(data, dict) or not data.get('headline') or not data.get('body'):
        raise AIProviderError('La IA no devolvió el texto ajustado. Revisa las fechas a mano.', kind='empty')
    out = {k: clean_meta(str(data.get(k) or '')) or fields[k] for k in fields}
    out['body'] = out['body'][:2200]
    out['changed'] = any(out[k] != fields[k] for k in fields)
    return out


def headline_line_count(headline, brand=None):
    """Líneas que ocupa el titular en la imagen del medio."""
    from . import brands
    b = brands.settings(brand)
    if b['slug'] != brands.DEFAULT and b.get('headline_lines'):
        c = int(b.get('line_chars') or 30)
        lines, cur = 0, ''
        for w in (headline or '').split():
            x = (cur + ' ' + w).strip()
            if len(x) <= c or not cur:
                cur = x
            else:
                lines, cur = lines + 1, w
        return lines + (1 if cur else 0)
    return len(layout.headline_lines(headline))


def headline_fits(headline, brand=None, max_lines=3):
    """¿Cabe el titular en la imagen con N líneas como mucho?"""
    return headline_line_count(headline, brand) <= max_lines


def headline_max_lines(brand=None):
    """InfoLinense tiene plantilla de 4 líneas con letra más pequeña: un titular largo cabe sin recortarlo."""
    from . import brands
    b = brands.settings(brand)
    if b['slug'] != brands.DEFAULT and b.get('headline_lines'):
        return int(b['headline_lines'])
    return layout.HEADLINE_MAX_LINES


def headline_target(brand=None):
    """(líneas, mínimo, máximo de caracteres) para que el titular llene la imagen."""
    from . import brands
    b = brands.settings(brand)
    if b['slug'] != brands.DEFAULT and b.get('headline_lines'):
        n, c = int(b['headline_lines']), int(b.get('line_chars') or 30)
        return n, c * (n - 1) + 4, c * n
    return 3, 52, 80


def shorten_headline(article, max_lines=3):
    """Ajusta el titular a la imagen: si pasa de 3 líneas, la IA lo acorta; si se queda en 1-2 líneas
    (demasiado corto), la IA lo completa con datos del texto hasta llenar las 3. Nunca inventa."""
    head = article.get('headline') or ''
    brand = article.get('brand')
    n, lo, hi = headline_target(brand)
    top = max(n, headline_max_lines(brand))  # 3 líneas o, si hay plantilla con letra más pequeña, hasta 4
    ok = lambda k: n <= k <= top
    lines = headline_line_count(head, brand)
    if ok(lines) or (lines < n and not AI_ENABLED):
        return head
    if AI_ENABLED:
        for attempt in range(3):
            lines = headline_line_count(head, brand)
            if ok(lines):
                return head
            if lines > top:
                limit = max(lo, min(hi, int(len(head) * (0.85 if attempt == 0 else 0.7))))
                task = (f'Este titular no cabe en la imagen. Recórtalo lo justo (quita solo palabras de relleno) para que quede '
                        f'entre {lo} y {limit} caracteres. Mantén todos los datos importantes (qué pasa, dónde, a quién afecta, cifras).')
            else:
                task = (f'Este titular se queda corto en la imagen (ocupa {lines} de {n} líneas). Reescríbelo más completo, '
                        f'entre {lo} y {hi} caracteres, añadiendo un dato concreto del texto (cifra, fecha, lugar, a quién afecta). '
                        'Solo datos que estén en la noticia.')
            prompt = f'''{task}
Español claro, sin inventar ni cambiar los hechos, sin comillas ni punto final, sin sensacionalismo.
Devuelve JSON {{"headline": "..."}}.
TITULAR: {head}
ENTRADILLA: {article.get('subtitle') or ''}
TEXTO: {(article.get('body') or '')[:1500]}'''
            try:
                data, _, _ = ask_json(SYSTEM, prompt, max_tokens=300)
                new = clean_meta(str((data or {}).get('headline') or '')).strip().rstrip('.')
            except AIProviderError:
                break
            if not new:
                break
            if ok(headline_line_count(new, brand)):
                return new
            if lines < n and headline_line_count(new, brand) > top:
                continue  # se ha pasado al alargar: se reintenta desde el original
            head = new
    if headline_fits(head, brand, max_lines):
        return head
    return layout.fit_headline(head) if headline_fits(layout.fit_headline(head), brand, 4) else head


def alternate_headlines(article):
    if not AI_ENABLED:
        raise AIProviderError('Para proponer titulares hace falta una clave de IA en Railway.', kind='config')
    prompt = f'''Propón exactamente siete titulares distintos para esta noticia siguiendo la sección TITULAR de la guía de estilo (cuentan la noticia, con cifras, lugares conocidos y consecuencias cuando existan). Directos, claros, fieles a los hechos del texto, útiles para vecinos de La Línea, sin sensacionalismo ni exclamaciones, máximo 80 caracteres cada uno (van tal cual en la imagen). Varía el enfoque (dato principal, a quién afecta, dónde, cuándo, consecuencia). No repitas el titular actual ni añadas datos que no estén en el texto.
TITULAR ACTUAL: {article.get('headline', '')}
ENTRADILLA: {article.get('subtitle', '')}
TEXTO: {(article.get('body') or '')[:2200]}
Devuelve solo JSON: {{"headlines": ["...", "...", "...", "...", "...", "...", "..."]}}'''
    for _ in range(2):
        data, provider, _ = ask_json(style_guide(), prompt, max_tokens=1200)
        clean = _clean_headlines(data.get('headlines') if isinstance(data, dict) else None, article.get('headline'))
        if len(clean) >= 7:
            return clean[:7]
    raise AIProviderError('La IA no devolvió siete titulares diferentes. Reintenta.', provider, kind='empty')


def generate_carousel(article):
    if not AI_ENABLED:
        raise AIProviderError('Para preparar un carrusel hace falta una clave de IA en Railway.', kind='config')
    prompt = f'''Decide si esta noticia funciona mejor como carrusel para redes de InfoLinense. Encaja solo si explica un proceso, requisitos, varias claves, cifras o consecuencias. Si no encaja, devuelve suitable=false, reason y slides=[].
Si encaja, devuelve suitable=true y entre 3 y 6 diapositivas: la primera presenta el tema; las siguientes, un hecho distinto cada una; la última, qué debe saber o hacer el vecino. Cada diapositiva: title (máximo 70 caracteres), text (máximo 220 caracteres, frases completas y correctas) y photo_query (qué fotografía real buscar). Usa solo datos del texto. Devuelve JSON con suitable, reason y slides.
TITULAR: {article.get('headline', '')}
ENTRADILLA: {article.get('subtitle', '')}
CUERPO: {(article.get('body') or '')[:2200]}
SECCIÓN: {article.get('section', '')}'''
    data, provider, _ = ask_json(SYSTEM, prompt, max_tokens=2500)
    suitable = bool(data.get('suitable'))
    slides = data.get('slides') if isinstance(data.get('slides'), list) else []
    normalized = []
    for slide in slides[:6]:
        if not isinstance(slide, dict):
            continue
        query = re.sub(r'\s+', ' ', str(slide.get('photo_query') or slide.get('photo_suggestion') or '')).strip()[:160]
        normalized.append({'title': re.sub(r'\s+', ' ', str(slide.get('title') or '')).strip()[:70],
                           'text': re.sub(r'\s+', ' ', str(slide.get('text') or '')).strip()[:220],
                           'photo_query': query, 'photo_suggestion': query})
    normalized = [s for s in normalized if s['title'] and s['text']]
    if suitable and len(normalized) < 3:
        raise AIProviderError('La IA no devolvió al menos tres diapositivas válidas. Reintenta.', provider, kind='empty')
    return {'suitable': suitable, 'reason': str(data.get('reason') or '')[:500], 'slides': normalized if suitable else [], 'provider': provider}


def discover_candidates():
    """Web discovery (regional, national, international with a local angle). Needs web search enabled."""
    prompt = '''Busca noticias, documentos y anuncios públicos de las últimas 48 horas que afecten de forma concreta a La Línea de la Concepción.
Busca sobre todo: licitaciones y adjudicaciones (Plataforma de Contratación, BOP Cádiz, BOJA, BOE), edictos, noticias de medios nacionales que mencionen La Línea, y NOTICIAS NACIONALES ADAPTABLES: medidas del Gobierno, la Junta o la UE, datos de paro, vivienda, ayudas, transporte, frontera con Gibraltar, sanidad o educación que tengan un efecto concreto para los vecinos de La Línea. En local_angle explica en una frase cómo se adapta a La Línea de la Concepción (empieza por «En La Línea de la Concepción…»).
«La Línea» es SOLO la ciudad de La Línea de la Concepción (Cádiz): descarta líneas de metro, tren, autobús, alta velocidad, líneas rojas, etc.
No inventes resultados: cada uno debe tener una URL real que hayas visto. Prioriza exclusivas, edictos, licitaciones y documentos oficiales. Evita duplicados y resultados donde «línea» no sea la ciudad.
Devuelve JSON con clave items: máximo 15 objetos con title, url, source_name, published_at (ISO o vacío), excerpt, local_angle (qué cambia en La Línea), scope (local, regional, nacional o internacional), official (boolean).'''
    data, _, _ = ask_json(SYSTEM, prompt, web=True, web_required=True, max_tokens=4000)
    items = data.get('items', []) if isinstance(data, dict) else []
    return [i for i in items if isinstance(i, dict) and str(i.get('url', '')).startswith('http')]
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
    section = layout.normalize_section('', title + ' ' + excerpt)
    subtitle = clip(details[0], 180).rstrip(' .') + '.' if details else ''
    image_headline = title if layout.headline_fits(title) else layout.fit_headline(title)
    return {'section': section, 'headline': title, 'image_headline': image_headline, 'subtitle': subtitle, 'body': body[:2200],
            'social_text': body[:2200], 'graphic_summary': layout.fit_summary(subtitle), 'ai_image_suggestion': ''}

