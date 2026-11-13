"""Isolated browser supervision fixtures; no Facebook accounts or network calls."""
import os
from pathlib import Path
import subprocess
import sys
import threading
import time


def controlled_worker(connection, profile):
    profile = Path(profile)
    while True:
        action, args = connection.recv()
        if args.get('crash'):
            os._exit(1)
        if args.get('block'):
            if args.get('child'):
                child = subprocess.Popen([sys.executable, '-c', 'import threading; threading.Event().wait()'])
                (profile / 'child-pid').write_text(str(child.pid))
            (profile / 'started').touch()
            threading.Event().wait()
        connection.send({'ok': True, 'result': {'done': True}, 'state': {'open': True, 'blocked_hosts': []}})


def chromium_worker(connection, profile):
    from app import facebook_browser as browser
    browser.DATA_DIR = Path(profile).parent
    browser._profile = Path(profile)
    try:
        while True:
            action, args = connection.recv()
            browser._ensure()
            if args.get('block'):
                (Path(profile) / 'started').touch()
                threading.Event().wait()
            browser._page.set_content('<p>Private browser supervision fixture</p>')
            if args.get('save_fixture_session'):
                browser._context.add_cookies([{'name': 'desk_fixture', 'value': 'fixture',
                    'domain': 'example.invalid', 'path': '/', 'expires': time.time() + 86400}])
            session = any(cookie['name'] == 'desk_fixture' for cookie in browser._context.cookies())
            content = browser._page.locator('p').inner_text()
            if args.get('save_fixture_session'):
                browser._close()  # Persist the synthetic fixture, as login confirmation does.
            connection.send({'ok': True, 'result': {'chromium': content, 'session_present': session},
                             'state': {'open': True, 'blocked_hosts': []}})
    finally:
        browser._close()
