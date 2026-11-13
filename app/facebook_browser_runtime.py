"""Bounded, exclusive execution for Desk's private Facebook browser.

Playwright lives in a disposable child process. An unresponsive command cannot
leave a permanent thread/queue behind. Only that process tree is terminated;
the persistent browser profile and saved login are never deleted by recovery.
"""
import multiprocessing
import os
from pathlib import Path
import signal
import threading
import time

from .social_publish import SocialError


class BrowserBusy(SocialError):
    pass


class BrowserUnavailable(SocialError):
    pass


def _identity(pid):
    try:
        fields = Path('/proc/%s/stat' % pid).read_text().rsplit(')', 1)[1].split()
        return int(fields[1]), fields[19]  # parent PID, process start time
    except (OSError, ValueError, IndexError):
        return None


def _descendants(root):
    """Snapshot ownership plus start times, so reused PIDs are never signalled."""
    processes = {}
    for path in Path('/proc').iterdir():
        if path.name.isdigit():
            identity = _identity(int(path.name))
            if identity:
                processes[int(path.name)] = identity
    owned = {root: processes[root]} if root in processes else {}
    while True:
        extra = {pid: identity for pid, identity in processes.items()
                 if pid not in owned and identity[0] in owned}
        if not extra:
            return owned
        owned.update(extra)


def _signal_owned(pid, identity, sig):
    current = _identity(pid)
    if current and current[1] == identity[1]:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass


def _terminate(process, connection):
    if process is not None and process.pid is not None:
        owned = (_descendants(process.pid) if process.pid and process.is_alive()
                 and _identity(process.pid) == getattr(process, '_desk_identity', None) else {})
        # Leaves first: let Chromium flush its profile before its driver exits.
        for pid, identity in reversed(list(owned.items())):
            _signal_owned(pid, identity, signal.SIGTERM)
        process.join(timeout=1)
        for pid, identity in reversed(list(owned.items())):
            _signal_owned(pid, identity, signal.SIGKILL)
        if process.is_alive():
            process.kill()
        process.join(timeout=1)
    if connection is not None:
        connection.close()


def _worker(connection, profile):
    from . import facebook_browser as browser
    browser._profile = Path(profile)
    try:
        while True:
            try:
                action, args = connection.recv()
            except EOFError:
                break
            try:
                result = browser._execute(action, args)
                response = {'ok': True, 'result': result}
            except SocialError as error:
                response = {'ok': False, 'message': str(error)}
            except Exception:
                # Do not transmit exception values, tracebacks, URLs or typed input.
                response = {'ok': False, 'message': 'No se pudo completar la operación del navegador privado de Facebook.'}
            response['state'] = browser._execute('status', {})
            connection.send(response)
            if action in {'close', 'disconnect'}:
                break
    finally:
        browser._close()
        connection.close()


class BrowserRuntime:
    def __init__(self, worker=_worker, command_timeout=60, read_timeout=120):
        self._worker = worker
        self._command_timeout, self._read_timeout = command_timeout, read_timeout
        self._operation_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._stop_lock = threading.RLock()
        self._process = self._connection = None
        self._action, self._started = '', 0
        self._state = {'open': False, 'blocked_hosts': []}

    def status(self):
        from . import db
        with self._state_lock:
            return dict(self._state, busy=bool(self._action), action=self._action,
                        elapsed_seconds=round(time.monotonic() - self._started) if self._action else 0,
                        enabled=db.get_setting('facebook_browser_enabled', '') == 'true',
                        width=1100, height=800)

    def _stop(self, process=None, connection=None):
        with self._stop_lock:
            with self._state_lock:
                if process is None:
                    process, connection = self._process, self._connection
                if process is self._process:
                    self._process = self._connection = None
                    self._state = {'open': False, 'blocked_hosts': []}
            _terminate(process, connection)

    def call(self, action, profile, **args):
        if action == 'status':
            return self.status()
        if action == 'close' and self._process is None:
            return {'ok': True}
        if action == 'restart':
            self._stop()
            return {'ok': True, 'message': 'Navegador recuperado. La sesión guardada se conserva.'}
        if not self._operation_lock.acquire(blocking=False):
            raise BrowserBusy('El navegador está leyendo o atendiendo otra operación. Puedes consultar su estado o recuperarlo sin borrar la sesión.')
        process = connection = None
        try:
            with self._state_lock:
                self._action, self._started = action, time.monotonic()
            with self._stop_lock:
                process, connection = self._process, self._connection
                if process is None or not process.is_alive():
                    if process is not None:
                        self._stop(process, connection)
                    context = multiprocessing.get_context('spawn')
                    connection, child = context.Pipe()
                    process = context.Process(target=self._worker, args=(child, str(profile)), daemon=True,
                                              name='facebook-private-browser')
                    try:
                        process.start()
                        process._desk_identity = _identity(process.pid)
                    finally:
                        child.close()
                    with self._state_lock:
                        self._process, self._connection = process, connection
            connection.send((action, args))
            timeout = self._read_timeout if action == 'read' else self._command_timeout
            if not connection.poll(timeout):
                self._stop(process, connection)
                raise BrowserUnavailable('El navegador no respondió a tiempo y se ha recuperado sin borrar la sesión. Vuelve a intentar la búsqueda.')
            response = connection.recv()
            with self._state_lock:
                if process is not self._process:
                    raise BrowserUnavailable('La operación se detuvo al recuperar el navegador. La sesión guardada se conserva.')
                self._state = response['state']
            if not response['ok']:
                raise SocialError(response['message'])
            return response['result']
        except (EOFError, BrokenPipeError, OSError, ValueError):
            self._stop(process, connection)
            raise BrowserUnavailable('La operación del navegador se interrumpió. La sesión guardada se conserva; puedes volver a buscar.') from None
        except RuntimeError:
            self._stop(process, connection)
            raise BrowserUnavailable('No se pudo iniciar el proceso privado del navegador. Revisa los recursos del servidor; la sesión guardada se conserva.') from None
        finally:
            with self._state_lock:
                self._action, self._started = '', 0
            self._operation_lock.release()
