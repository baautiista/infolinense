const $ = (id) => document.getElementById(id);
const sessionKey = 'infolinense_token';
const importPath = '/api/import/chatgpt/draft';
let token = localStorage.getItem(sessionKey);
let articleId = null;
let draft;

function readDraft() {
  const match = /^#v1\.([A-Za-z0-9_-]{1,90000})$/.exec(location.hash);
  if (!match) throw new Error('Abre «Enviar a Desk» desde una noticia de ChatGPT.');
  const bytes = Uint8Array.from(atob(match[1].replace(/-/g, '+').replace(/_/g, '/')), (char) => char.charCodeAt(0));
  const value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  if (!value?.headline || !value?.subtitle || !value?.body) throw new Error('El enlace de la noticia está incompleto.');
  return value;
}

async function post(path, data) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(path, { method: 'POST', headers, body: JSON.stringify(data) });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = typeof result.detail === 'string' ? result.detail : 'Desk no ha podido completar el envío.';
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return result;
}

async function send() {
  $('loginForm').hidden = true;
  $('retry').hidden = true;
  try {
    if (!articleId) {
      $('status').textContent = 'Guardando el borrador en Desk…';
      const saved = await post(importPath, draft);
      articleId = saved.id;
      $('edit').href = '/#editor/' + articleId;
      $('edit').hidden = false;
    }
    $('status').textContent = 'Borrador guardado. Creando el diseño en Canva…';
    const design = await post(`${importPath}/${articleId}/canva`);
    $('canva').href = design.url;
    $('canva').hidden = false;
    $('status').textContent = design.needs_photo
      ? 'Borrador guardado y diseño rellenado en Canva. Puedes añadir la foto en Canva.'
      : 'Borrador guardado y diseño rellenado en Canva.';
  } catch (error) {
    if (error.status === 401) {
      token = null;
      localStorage.removeItem(sessionKey);
      showLogin();
      return;
    }
    $('status').textContent = articleId ? 'Borrador guardado. ' + error.message : error.message;
    $('retry').textContent = articleId ? 'Reintentar Canva' : 'Reintentar envío';
    $('retry').hidden = false;
  }
}

function showLogin() {
  $('status').textContent = 'Entra en Desk para completar el envío.';
  $('loginForm').hidden = false;
}

$('loginForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = event.target.querySelector('button');
  button.disabled = true;
  try {
    const session = await post('/api/auth/login', { password: $('password').value });
    token = session.token;
    $('password').value = '';
    localStorage.setItem(sessionKey, token);
    await send();
  } catch (error) {
    $('status').textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

$('retry').addEventListener('click', send);
try {
  draft = readDraft();
  $('headline').textContent = draft.headline;
  $('headline').hidden = false;
  if (token) send();
  else showLogin();
} catch {
  $('status').textContent = 'Abre un enlace válido de «Enviar a Desk» desde ChatGPT.';
}
