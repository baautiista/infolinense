"""Alertas al móvil: notificaciones push del navegador (Web Push, sin servicios de pago) y, si se configura, Telegram.

Avisa de: noticias urgentes nuevas, publicaciones programadas que fallan, novedades de temas que sigues y
eventos de la agenda que llegan. Las claves VAPID las genera el propio panel la primera vez.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import struct
import time
from urllib.parse import urlparse

import requests

from . import db

KINDS = {'urgent': 'Noticias urgentes', 'failed': 'Fallos al publicar', 'topic': 'Novedades de temas que sigues',
         'agenda': 'Agenda: eventos que llegan'}
URGENT = re.compile(r'\b(incendio|fuego|accidente|herid[oa]s?|fallec\w+|muert[oa]s?|detenid[oa]s?|desalojo|desalojad\w+|'
                    r'atropell\w+|tiroteo|apu[nñ]alad\w+|rescate|rescatad\w+|desaparecid[oa]|explosi[oó]n|derrumbe|'
                    r'corte\s+de\s+(agua|luz|tr[aá]fico)|sin\s+(agua|luz)|cierre\s+de\s+la\s+(frontera|verja)|colas?\s+en\s+la\s+(frontera|verja)|'
                    r'alerta\s+(naranja|roja)|temporal|inundaci\w+|evacua\w+|[uú]ltima\s+hora|urgente)\b', re.I)


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def _unb64(s):
    s = str(s or '')
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def _ensure():
    db.exec_('''CREATE TABLE IF NOT EXISTS push_subs(endpoint TEXT PRIMARY KEY, p256dh TEXT NOT NULL, auth TEXT NOT NULL,
                label TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    db.exec_('''CREATE TABLE IF NOT EXISTS alerts_sent(key TEXT PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')


# ---------- claves VAPID ----------

def _keys():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    pem = db.get_setting('vapid_private', '')
    if pem:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
    else:
        key = ec.generate_private_key(ec.SECP256R1())
        db.set_setting('vapid_private', key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                          serialization.NoEncryption()).decode())
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return key, pub


def public_key():
    return _b64(_keys()[1])


def _vapid_header(endpoint):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    key, pub = _keys()
    u = urlparse(endpoint)
    head = _b64(json.dumps({'typ': 'JWT', 'alg': 'ES256'}, separators=(',', ':')).encode())
    body = _b64(json.dumps({'aud': '%s://%s' % (u.scheme, u.netloc), 'exp': int(time.time()) + 12 * 3600,
                            'sub': 'mailto:' + os.getenv('ALERTS_EMAIL', 'alertas@infolinense.com')}, separators=(',', ':')).encode())
    r, s = decode_dss_signature(key.sign(('%s.%s' % (head, body)).encode(), ec.ECDSA(hashes.SHA256())))
    sig = _b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))
    return 'vapid t=%s.%s.%s, k=%s' % (head, body, sig, _b64(pub))


# ---------- cifrado aes128gcm (RFC 8291) ----------

def _hkdf(salt, ikm, info, length):
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b'\x01', hashlib.sha256).digest()[:length]


def encrypt(payload, p256dh, auth, salt=None, sender=None):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    ua_pub = _unb64(p256dh)
    secret = _unb64(auth)
    sender = sender or ec.generate_private_key(ec.SECP256R1())
    as_pub = sender.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = sender.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub))
    ikm = _hkdf(secret, shared, b'WebPush: info\x00' + ua_pub + as_pub, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b'Content-Encoding: aes128gcm\x00', 16)
    nonce = _hkdf(salt, ikm, b'Content-Encoding: nonce\x00', 12)
    cipher = AESGCM(cek).encrypt(nonce, payload + b'\x02', None)
    return salt + struct.pack('!IB', 4096, len(as_pub)) + as_pub + cipher


# ---------- suscripciones y envío ----------

def subscribe(sub, label=''):
    _ensure()
    keys = sub.get('keys') or {}
    if not (str(sub.get('endpoint', '')).startswith('https://') and keys.get('p256dh') and keys.get('auth')):
        raise ValueError('Suscripción no válida')
    db.exec_('INSERT OR REPLACE INTO push_subs(endpoint,p256dh,auth,label) VALUES(?,?,?,?)',
             (sub['endpoint'], keys['p256dh'], keys['auth'], (label or '')[:80]))


def unsubscribe(endpoint):
    _ensure()
    db.exec_('DELETE FROM push_subs WHERE endpoint=?', (endpoint,))


def devices():
    _ensure()
    return db.rows('SELECT label,created_at,substr(endpoint,1,40) endpoint FROM push_subs ORDER BY created_at DESC')


def prefs():
    try:
        data = json.loads(db.get_setting('alert_prefs', '') or '{}')
    except ValueError:
        data = {}
    return {k: bool(data.get(k, True)) for k in KINDS}


def set_prefs(data):
    db.set_setting('alert_prefs', json.dumps({k: bool(data.get(k, True)) for k in KINDS}))
    return prefs()


def _send_push(row, data):
    body = encrypt(json.dumps(data, ensure_ascii=False).encode(), row['p256dh'], row['auth'])
    r = requests.post(row['endpoint'], data=body, timeout=15, headers={
        'TTL': '86400', 'Content-Encoding': 'aes128gcm', 'Content-Type': 'application/octet-stream',
        'Urgency': 'high', 'Authorization': _vapid_header(row['endpoint'])})
    if r.status_code in (404, 410):
        unsubscribe(row['endpoint'])  # el móvil ya no acepta avisos de este panel
    return r.status_code < 300


def _telegram(text):
    token, chat = os.getenv('TELEGRAM_BOT_TOKEN', '').strip(), os.getenv('TELEGRAM_CHAT_ID', '').strip()
    if not (token and chat):
        return False
    try:
        r = requests.post('https://api.telegram.org/bot%s/sendMessage' % token, timeout=15,
                          json={'chat_id': chat, 'text': text, 'disable_web_page_preview': True})
        return r.ok
    except requests.RequestException:
        return False


def notify(kind, title, body='', url='/', key=None, force=False):
    """Manda el aviso a todos los móviles suscritos (y a Telegram si está). key evita repetir el mismo aviso."""
    _ensure()
    if not force and not prefs().get(kind, True):
        return 0
    if key:
        if db.row('SELECT 1 FROM alerts_sent WHERE key=?', (key,)):
            return 0
        db.exec_('INSERT OR IGNORE INTO alerts_sent(key) VALUES(?)', (key,))
    sent = 0
    data = {'title': title[:120], 'body': body[:240], 'url': url or '/', 'tag': kind}
    for row in db.rows('SELECT * FROM push_subs'):
        try:
            sent += 1 if _send_push(row, data) else 0
        except Exception as exc:
            db.log('alert_error', str(exc)[:200])
    if _telegram(title + ('\n' + body if body else '') + ('\n' + url if url and url.startswith('http') else '')):
        sent += 1
    return sent


def check_new(candidate_ids):
    """Tras una búsqueda: avisa de lo urgente (sucesos, cortes, frontera, alertas) y de lo nuevo del Ayuntamiento urgente."""
    for cid in candidate_ids or []:
        c = db.row('SELECT id,title,excerpt,source_name,status FROM candidates WHERE id=?', (cid,))
        if not c or c.get('status') in ('archived', 'merged'):
            continue
        if URGENT.search((c.get('title') or '') + ' ' + (c.get('excerpt') or '')[:300]):
            notify('urgent', 'Urgente: ' + (c['title'] or '')[:100], c.get('source_name') or '', '/#sort', key='urgent:%s' % cid)
