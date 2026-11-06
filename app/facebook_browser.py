"""A private Facebook browser controlled by the authenticated editor.

Uses a dedicated profile created by Desk, never an existing personal browser profile.
No password/cookie export endpoints, remote debugging ports, or credential logging.
All Playwright work stays on one worker thread. The profile lives in the data volume.
"""
from concurrent.futures import Future, TimeoutError
import json
import os
from pathlib import Path
import queue
import shutil
import threading
from urllib.parse import urlparse

from .config import DATA_DIR
from .social_publish import SocialError

_WIDTH, _HEIGHT = 1100, 800
_commands = queue.Queue(maxsize=32)
_thread = None
_start_lock = threading.Lock()
_context = _page = _playwright = None
_interactive = False
_blocked_hosts = set()
_profile = Path(DATA_DIR) / '.private-facebook-browser'


def _worker():
    while True:
        future, action, args = _commands.get()
        if not future.set_running_or_notify_cancel():
            continue
        try:
            future.set_result(_execute(action, args))
        except SocialError as exc:
            future.set_exception(exc)
        except Exception:
            # Browser errors may contain URLs or input values: do not return/log them.
            future.set_exception(SocialError('No se pudo completar la operación del navegador privado de Facebook.'))


def call(action, **args):
    global _thread
    with _start_lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_worker, daemon=True, name='facebook-private-browser')
            _thread.start()
    future = Future()
    try:
        _commands.put_nowait((future, action, args))
    except queue.Full:
        raise SocialError('El navegador está ocupado; espera a que termine la operación actual.')
    try:
        return future.result(timeout=180 if action == 'read' else 60)
    except TimeoutError:
        future.cancel()
        raise SocialError('El navegador sigue ocupado. Espera antes de repetir la operación.') from None


def _allowed_url(url):
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    return parsed.scheme == 'https' and not parsed.username and not parsed.password and (host == 'facebook.com' or host.endswith('.facebook.com') or host.endswith('.fbcdn.net') or host.endswith('.facebook.net'))


def _allowed_resource_url(url, interactive=False):
    if _allowed_url(url):
        return True
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    # These are resources for a challenge completed by the editor, never a solver.
    return (interactive and parsed.scheme == 'https' and not parsed.username and not parsed.password and
            any(host == domain or host.endswith('.' + domain) for domain in ('arkoselabs.com', 'funcaptcha.com')))


def _route_request(route):
    request = route.request
    allowed = _allowed_resource_url(request.url, _interactive)
    if allowed and request.is_navigation_request():
        # Arkose is permitted in a frame, not as a replacement top-level site.
        try:
            if request.frame.parent_frame is None:
                allowed = _allowed_url(request.url)
        except Exception:
            allowed = False
    if allowed:
        route.continue_()
    else:
        host = urlparse(request.url).hostname
        if host and len(_blocked_hosts) < 16:
            _blocked_hosts.add(host)
        route.abort()


def _ensure():
    global _context, _page, _playwright
    if _context is not None:
        return
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SocialError('El navegador privado necesita Playwright y Chromium instalados en el servidor.')
    if not _profile.resolve().is_relative_to(Path(DATA_DIR).resolve()):
        raise SocialError('La carpeta de la sesión de Facebook no está dentro del volumen privado de datos.')
    _profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    _profile.chmod(0o700)
    executable = os.getenv('FACEBOOK_BROWSER_EXECUTABLE', '/usr/bin/chromium')
    if not Path(executable).is_file():
        raise SocialError('Chromium no está instalado en el servidor de Desk.')
    _playwright = sync_playwright().start()
    options = {'executable_path': executable, 'headless': True, 'viewport': {'width': _WIDTH, 'height': _HEIGHT},
               'locale': 'es-ES', 'args': ['--no-sandbox', '--disable-dev-shm-usage']}
    proxy = os.getenv('HTTPS_PROXY') or os.getenv('HTTP_PROXY')
    if proxy:
        options['proxy'] = {'server': proxy}
    try:
        _context = _playwright.chromium.launch_persistent_context(str(_profile), **options)
        _context.route('**/*', _route_request)
        _page = _context.pages[0] if _context.pages else _context.new_page()
        _page.set_default_timeout(12000)
    except Exception:
        if _context:
            _context.close()
        _context = None
        _playwright.stop()
        _playwright = None
        raise SocialError('No se pudo iniciar Chromium. Comprueba su instalación y la memoria del servidor.') from None


def _close():
    global _context, _page, _playwright, _interactive
    _interactive = False
    if _context:
        _context.close()
    if _playwright:
        _playwright.stop()
    _context = _page = _playwright = None


def _execute(action, args):
    global _interactive
    from . import db
    if action == 'status':
        return {'open': _context is not None, 'enabled': db.get_setting('facebook_browser_enabled', '') == 'true',
                'width': _WIDTH, 'height': _HEIGHT, 'blocked_hosts': sorted(_blocked_hosts)}
    if action == 'disconnect':
        _close()
        if _profile.exists():
            shutil.rmtree(_profile)
        db.set_setting('facebook_browser_enabled', '')
        return {'ok': True}
    if action == 'read' and db.get_setting('facebook_browser_enabled', '') != 'true':
        raise SocialError('Conecta primero tu sesión privada de Facebook desde Ajustes de Desk.')
    if action not in {'start', 'read'} and _context is None:
        raise SocialError('Abre primero el navegador privado de Facebook.')
    if action == 'start':
        _interactive = True
        _blocked_hosts.clear()
    elif action == 'read':
        _interactive = False
        _blocked_hosts.clear()
    _ensure()
    if action == 'start':
        source = db.row("SELECT url FROM sources WHERE brand='cofrade' AND kind='social' AND active=1 AND official=1 ORDER BY name LIMIT 1")
        target = source['url'] if source and _allowed_url(source['url']) else 'https://www.facebook.com/'
        _page.goto(target, wait_until='domcontentloaded', timeout=45000)
        _page.wait_for_timeout(1500)
        return {'open': True, 'width': _WIDTH, 'height': _HEIGHT}
    if action == 'confirm':
        if not _allowed_url(_page.url):
            raise SocialError('El navegador no está en Facebook.')
        if urlparse(_page.url).path.startswith(('/login', '/checkpoint')) or _page.locator('input[type=password]:visible').count():
            raise SocialError('Facebook sigue mostrando el formulario de acceso. Termina el inicio de sesión en el navegador privado.')
        names = {cookie['name'] for cookie in _context.cookies('https://www.facebook.com')}
        if not {'c_user', 'xs'}.issubset(names):
            raise SocialError('No se ha detectado una sesión iniciada de Facebook. Completa el acceso antes de confirmar.')
        db.set_setting('facebook_browser_enabled', 'true')
        _close()  # Flush the private session and free browser memory while idle.
        return {'ok': True}
    if action == 'screenshot':
        return _page.screenshot(type='png')
    if action == 'input':
        kind = args.get('kind')
        if kind == 'click':
            x, y = args.get('x'), args.get('y')
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)) or not 0 <= x < _WIDTH or not 0 <= y < _HEIGHT:
                raise SocialError('Coordenadas fuera del navegador.')
            _page.mouse.click(x, y)
        elif kind == 'text':
            text = args.get('text')
            if not isinstance(text, str) or len(text) > 512:
                raise SocialError('La entrada del navegador no es válida.')
            _page.keyboard.insert_text(text)
        elif kind == 'key':
            key = args.get('key')
            if key not in {'Enter', 'Tab', 'Shift+Tab', 'Backspace', 'Delete', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp', 'PageDown'}:
                raise SocialError('Tecla no admitida por el navegador privado.')
            _page.keyboard.press(key)
        elif kind == 'scroll':
            delta = args.get('delta')
            if not isinstance(delta, (int, float)):
                raise SocialError('Desplazamiento no válido.')
            _page.mouse.wheel(0, max(-1600, min(1600, delta)))
        else:
            raise SocialError('Operación de navegador no admitida.')
        return {'ok': True}
    if action == 'read':
        try:
            return _read(args.get('url'))
        finally:
            _close()
    raise SocialError('Operación de navegador no admitida.')


def _read(url):
    from .facebook_public import html_payloads, parse_payloads
    from .facebook_reader import account
    if not isinstance(url, str) or not account(url):
        raise SocialError('La fuente no es una página de Facebook válida.')
    names = {cookie['name'] for cookie in _context.cookies('https://www.facebook.com')}
    if not {'c_user', 'xs'}.issubset(names):
        raise SocialError('La sesión privada no está iniciada o ha caducado. Vuelve a conectar Facebook en Ajustes.')
    payloads = []

    def receive(response):
        if '/api/graphql/' not in response.url:
            return
        try:
            text = response.text()
            if text.startswith('for (;;);'):
                text = text[9:]
            for line in text.splitlines():
                try:
                    data = json.loads(line)
                    if isinstance(data, dict):
                        payloads.append(data)
                except ValueError:
                    pass
        except Exception:
            pass

    _page.on('response', receive)
    try:
        _page.goto(url, wait_until='domcontentloaded', timeout=45000)
        _page.wait_for_timeout(1000)
        if '/login' in _page.url or _page.locator('input[type=password]:visible').count():
            raise SocialError('Facebook pide volver a iniciar sesión; abre la conexión privada en Ajustes.')
        payloads.extend(html_payloads(_page.content()))
        initial = parse_payloads(payloads, url)
        items = {post['id']: post for post in initial['items']}
        stagnant = 0
        for _ in range(20):
            previous = len(items)
            _page.mouse.wheel(0, 1400)
            _page.wait_for_timeout(1000)
            if payloads:
                try:
                    extra = parse_payloads(payloads, url)
                    items.update({post['id']: post for post in extra['items']})
                except SocialError:
                    pass
            if len(items) == previous:
                stagnant += 1
            else:
                stagnant = 0
            if stagnant >= 4:
                break
        return {'items': sorted(items.values(), key=lambda post: post['published_at'], reverse=True),
                'partial': True, 'message': 'Lectura con sesión privada. La cobertura del historial sigue pendiente de verificar; detener el desplazamiento no demuestra que Facebook haya entregado todos los posts.'}
    finally:
        _page.remove_listener('response', receive)
