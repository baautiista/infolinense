import base64, hashlib, hmac, json, os, time
from fastapi import Header, HTTPException
from .config import ADMIN_PASSWORD, JWT_SECRET, TOKEN_TTL_HOURS


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()

def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))

def make_token() -> str:
    payload = {'sub': 'admin', 'exp': int(time.time()) + TOKEN_TTL_HOURS * 3600}
    body = _b64(json.dumps(payload, separators=(',', ':')).encode())
    sig = _b64(hmac.new(JWT_SECRET.encode(), body.encode(), hashlib.sha256).digest())
    return body + '.' + sig

def verify_token(token: str) -> bool:
    try:
        body, sig = token.split('.', 1)
        expected = _b64(hmac.new(JWT_SECRET.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected): return False
        payload = json.loads(_unb64(body))
        return payload.get('sub') == 'admin' and int(payload.get('exp', 0)) > int(time.time())
    except Exception:
        return False

def login(password: str) -> str:
    if not ADMIN_PASSWORD or not JWT_SECRET:
        raise HTTPException(503, 'Configura ADMIN_PASSWORD y JWT_SECRET en el servidor')
    if not hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode()):
        raise HTTPException(401, 'Contraseña incorrecta')
    return make_token()

def require_auth(authorization: str | None = Header(default=None)):
    if not JWT_SECRET:
        raise HTTPException(503, 'Configura JWT_SECRET en el servidor')
    if not authorization or not authorization.lower().startswith('bearer '):
        raise HTTPException(401, 'Sesión requerida')
    token = authorization.split(' ', 1)[1].strip()
    if not verify_token(token):
        raise HTTPException(401, 'Sesión no válida o caducada')
    return True
