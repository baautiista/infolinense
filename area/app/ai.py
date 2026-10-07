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
                             'user_location': {'type': 'approximate', 'city': 'Algeciras',
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


