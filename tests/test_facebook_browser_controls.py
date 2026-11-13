import base64
import shutil
import unittest
from io import BytesIO
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright


class BrowserControlTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('chromium'), 'Chromium is not installed')
    def test_busy_poll_recovers_and_pending_input_is_not_replayed(self):
        source=Path('static/app.js').read_text().split('function armFacebookBrowser(v) {',1)[1]
        source='function armFacebookBrowser(v) {'+source
        buf=BytesIO(); Image.new('RGB',(2,2),'white').save(buf,format='PNG')
        png=base64.b64encode(buf.getvalue()).decode()
        with sync_playwright() as driver:
         browser=driver.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
         page=browser.new_page()
         page.set_content('''<p id="fbBrowserStatus"></p><button id="fbBrowserStart">Abrir</button><button id="fbBrowserConfirm">Confirmar</button><button id="fbBrowserRestart">Recuperar</button><button id="fbBrowserOff">Desconectar</button><div id="fbBrowserFrame" hidden><form id="fbBrowserSend"><input id="fbBrowserText"><button>Enviar</button></form><canvas id="fbBrowserCanvas" width="1100" height="800"></canvas></div>''')
         page.evaluate('''png => {
         window.state={open:false,busy:false,enabled:true}; window.screenshots=0; window.inputs=[];
         window.api=async (path, options) => {
          if (path.endsWith('/status')) return {...window.state};
          if (path.endsWith('/start')) {state.open=true; state.busy=true;state.action='read';return {open:true}};
          if (path.endsWith('/restart')) {state.open=false; state.busy=false;return {ok:true}};
          if (path.endsWith('/input')) {inputs.push(JSON.parse(options.body));return new Promise(resolve=>window.finishInput=resolve)};
          return {ok:true};
         };
         window.request=async()=> {screenshots++; if(screenshots===1){const error=new Error('Retryable busy');error.status=409;throw error};return new Response(Uint8Array.from(atob(png), c=>c.charCodeAt(0)),{headers:{'Content-Type':'image/png'}})};
         }''',png)
         page.add_script_tag(content=source)
         page.evaluate('armFacebookBrowser(document.body)')
         page.locator('#fbBrowserStart').click()
         page.wait_for_function("document.querySelector('#fbBrowserStatus').textContent.includes('Leyendo')")
         assert page.evaluate('screenshots')==0
         page.evaluate('state.busy=false')
         page.wait_for_function('screenshots>=2',timeout=6000)
         for text in ('fixture-first-input','fixture-stale-input'):
          page.locator('#fbBrowserText').fill(text)
          page.locator('#fbBrowserSend').dispatch_event('submit')
         page.wait_for_function('inputs.length===1')
         page.locator('#fbBrowserRestart').click()
         page.evaluate('finishInput({ok:true})')
         page.wait_for_timeout(100)
         assert page.evaluate('inputs.length')==1
         assert page.locator('#fbBrowserFrame').evaluate('(element)=>element.hidden')
         assert 'conserva' in page.locator('#fbBrowserStatus').inner_text()
         browser.close()
