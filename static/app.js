/* InfoLinense Desk 3 — panel móvil: Noticias, Agenda, Redacción y Ajustes. */
const $ = s => document.querySelector(s);
let token = localStorage.getItem('infolinense_token') || '';
let view = 'news', editorId = null, group = 'Todas', onlyToday = false;
let blobUrls = [];
const polling = {};
const cache = { photos: {}, article: {} };

const PRIO = [['urgent', 'Urgente'], ['today', 'Hoy'], ['this_week', 'Semana'], ['future', 'Futura']];
const PRIO_LABEL = Object.fromEntries(PRIO);
const GROUPS = ['Ayuntamiento', 'Licitaciones y edictos', 'Otros medios', 'Nacionales adaptables'];
const SECTIONS = [['OBRAS', '#0150FE', '#fff'], ['CIUDAD', '#4F9AFC', '#fff'], ['GIBRALTAR', '#7249E0', '#fff'], ['SUCESOS', '#D02132', '#fff'],
  ['CULTURA', '#E72E79', '#fff'], ['DEPORTES', '#00AB4F', '#fff'], ['COMERCIO', '#FF8E1A', '#fff'], ['MEDIO AMBIENTE', '#62DBD1', '#061E5C'],
  ['POLÍTICA', '#08176E', '#fff'], ['SOCIEDAD', '#2756CD', '#fff'], ['PATRIMONIO', '#B9831E', '#fff'], ['AGENDA', '#FDE206', '#061E5C']];
const SEC = Object.fromEntries(SECTIONS.map(s => [s[0], s]));
const STATUS = { draft: 'Borrador', review_ready: 'Por revisar', approved: 'Aprobada', published: 'Publicada' };

/* ---------- utilidades ---------- */
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m])) }
function safeUrl(s) { try { const u = new URL(s); return ['http:', 'https:'].includes(u.protocol) ? esc(u.href) : '#' } catch { return '#' } }
function jparse(s, d) { try { return JSON.parse(s || '') ?? d } catch { return d } }
function host(u) { try { return new URL(u).hostname.replace('www.', '') } catch { return '' } }
function toast(t) { const el = $('#toast'); el.textContent = t; el.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => el.hidden = true, 3200) }
function ago(iso) {
  if (!iso) return '';
  const d = new Date(iso), now = new Date(), min = Math.round((now - d) / 60000);
  if (min < 60) return `hace ${Math.max(1, min)} min`;
  if (min < 600) return `hace ${Math.round(min / 60)} h`;
  const day = d.toLocaleDateString('es-ES', { timeZone: 'Europe/Madrid' }) === now.toLocaleDateString('es-ES', { timeZone: 'Europe/Madrid' }) ? 'hoy'
    : d.toLocaleDateString('es-ES', { timeZone: 'Europe/Madrid', day: 'numeric', month: 'short' });
  return `${day} ${d.toLocaleTimeString('es-ES', { timeZone: 'Europe/Madrid', hour: '2-digit', minute: '2-digit' })}`;
}
function isToday(iso) { return iso && new Date(iso).toLocaleDateString('es-ES', { timeZone: 'Europe/Madrid' }) === new Date().toLocaleDateString('es-ES', { timeZone: 'Europe/Madrid' }) }
function secBadge(name) { const s = SEC[name] || SEC.CIUDAD; return `<span class="sec" style="background:${s[1]};color:${s[2]}">${esc(s[0])}</span>` }
function special(n) { const t = `${n.title} ${n.excerpt || ''} ${n.source_name || ''}`.toLowerCase(); if (n.source_kind === 'procurement' || /licitaci|contrataci|adjudicaci/.test(t)) return 'Licitación'; if (n.source_kind === 'bop' || /edicto/.test(t)) return 'Edicto'; if (/exclusiv/.test(t)) return 'Exclusiva'; return '' }
const wait = ms => new Promise(r => setTimeout(r, ms));

async function request(url, opt = {}) {
  const headers = { ...(opt.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }), ...(token ? { Authorization: `Bearer ${token}` } : {}) };
  const r = await fetch(url, { ...opt, headers });
  if (r.status === 401 && url !== '/api/auth/login') { logout(); throw new Error('La sesión ha caducado') }
  if (!r.ok) { let d = await r.text(); try { d = JSON.parse(d).detail || d } catch { } throw new Error(typeof d === 'string' ? d : JSON.stringify(d)) }
  return r;
}
async function api(url, opt) { const r = await request(url, opt); return r.headers.get('content-type')?.includes('json') ? r.json() : r.text() }
async function blobUrl(url) { const u = URL.createObjectURL(await (await request(url)).blob()); blobUrls.push(u); return u }
async function download(url, name) { try { const a = document.createElement('a'); a.href = await blobUrl(url); a.download = name; a.click() } catch (e) { toast(e.message) } }

/* ---------- sesión y navegación ---------- */
function logout() { token = ''; localStorage.removeItem('infolinense_token'); $('#app').hidden = true; $('#login').hidden = false }
$('#loginForm').onsubmit = async e => {
  e.preventDefault(); $('#loginError').textContent = '';
  try { token = (await api('/api/auth/login', { method: 'POST', body: JSON.stringify({ password: $('#password').value }) })).token; localStorage.setItem('infolinense_token', token); $('#password').value = ''; start() }
  catch (err) { $('#loginError').textContent = err.message }
};
function start() { $('#login').hidden = true; $('#app').hidden = false; go(location.hash.slice(1) || 'news') }
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => go(b.dataset.view));
$('#backBtn').onclick = () => history.length > 1 ? history.back() : go('drafts');
window.onhashchange = () => { const h = location.hash.slice(1); if (h && h !== currentHash()) go(h, true) };
function currentHash() { return view === 'editor' ? `editor/${editorId}` : view }
function go(target, fromHash) {
  const [v, id] = target.split('/');
  view = ['news', 'social', 'agenda', 'drafts', 'settings', 'editor'].includes(v) ? v : 'news';
  editorId = view === 'editor' ? Number(id) : null;
  if (!fromHash) location.hash = currentHash();
  document.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('on', b.dataset.view === (view === 'editor' ? 'drafts' : view)));
  $('#title').textContent = { news: 'Noticias', social: 'Redes', agenda: 'Agenda', drafts: 'Redacción', settings: 'Ajustes', editor: 'Noticia' }[view];
  $('#backBtn').hidden = view !== 'editor';
  $('#scanBtn').hidden = !['news', 'social'].includes(view);
  $('#scanBtn').textContent = view === 'social' ? 'Investigar redes' : 'Buscar noticias';
  render();
}
async function render() {
  blobUrls.forEach(URL.revokeObjectURL); blobUrls = [];
  const v = $('#view'); v.innerHTML = '<p class="empty">Cargando…</p>'; window.scrollTo(0, 0);
  try { await ({ news, social: socialView, agenda, drafts, settings, editor }[view])(v) }
  catch (e) { if (token) v.innerHTML = `<p class="empty">${esc(e.message)}</p>` }
}

/* ---------- Noticias ---------- */
$('#scanBtn').onclick = async () => {
  const b = $('#scanBtn'), social = view === 'social'; b.disabled = true; b.textContent = social ? 'Investigando…' : 'Buscando…';
  try {
    const r = await api(social ? '/api/social/scan' : '/api/scan', { method: 'POST' });
    toast(r.busy ? 'Ya hay una búsqueda en marcha' : `${r.added.length} ${social ? 'publicaciones' : 'noticias'} nuevas`); render()
  } catch (e) { toast(e.message) } finally { b.disabled = false; b.textContent = social ? 'Investigar redes' : 'Buscar noticias' }
};
let newsCache = [];
async function news(v) {
  newsCache = await api('/api/candidates?status=pending');
  drawNews(v);
}
function drawNews(v = $('#view')) {
  const pool = onlyToday ? newsCache.filter(n => isToday(n.date_iso)) : newsCache;
  const count = g => pool.filter(n => g === 'Todas' || n.group === g).length;
  const groups = ['Todas', ...GROUPS.filter(g => count(g))];
  if (!groups.includes(group)) group = 'Todas';
  const items = pool.filter(n => group === 'Todas' || n.group === group);
  v.innerHTML = `<div class="filters"><div class="chips scroll">${groups.map(g => `<button class="chip ${g === group ? 'on' : ''}" data-g="${esc(g)}">${esc(g)} <b>${count(g)}</b></button>`).join('')}</div>
    <label class="switch"><input type="checkbox" id="today" ${onlyToday ? 'checked' : ''}> Solo de hoy</label></div>
    ${items.length ? `<div class="list">${items.map(card).join('')}</div>` : `<p class="empty">No hay noticias ${onlyToday ? 'de hoy ' : ''}en ${group === 'Todas' ? 'ninguna fuente' : esc(group)}. Pulsa «Buscar noticias».</p>`}`;
  v.querySelectorAll('[data-g]').forEach(b => b.onclick = () => { group = b.dataset.g; drawNews() });
  $('#today').onchange = e => { onlyToday = e.target.checked; drawNews() };
  items.forEach(n => { if ((n.work_state === 'queued' || n.work_state === 'working') && !n.article_id) poll(n.id) });
}
function card(n) {
  const prio = PRIO_LABEL[n.editorial_priority] ? n.editorial_priority : '';
  const sp = special(n);
  return `<article class="item" id="c-${n.id}">
    <div class="meta">${n.social_type ? `<span class="kind ${n.social_type}">${{ queja: 'Queja', propuesta: 'Propuesta', asociacion: 'Asociación' }[n.social_type] || ''}</span>` : ''}<span class="src">${esc(n.outlet || n.source_name || host(n.url))}</span><span>${ago(n.date_iso)}</span>${sp ? `<span class="flag">${sp}</span>` : ''}</div>
    <h3><a href="${safeUrl(n.url)}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a></h3>
    ${n.local_angle ? `<p class="angle">${esc(n.local_angle)}</p>` : ''}
    <div class="chips">${PRIO.map(([k, t]) => `<button class="chip ${prio === k ? 'on' : ''}" onclick="setPriority(${n.id},'${k}')">${t}</button>`).join('')}<button class="chip no" onclick="setPriority(${n.id},'no_interest')" aria-label="No interesa" title="No interesa">✕</button></div>
    <div class="state" id="s-${n.id}">${stateLine(n)}</div></article>`;
}
function stateLine(n) {
  if (n.article_id) return `<button class="btn primary small" onclick="go('editor/${n.article_id}')">Abrir borrador</button><span class="muted">${n.planned_at ? 'Agenda: ' + esc(String(n.planned_at).slice(11, 16)) : ''}</span>`;
  if (n.work_state === 'queued' || n.work_state === 'working') return `<span class="spin"></span><span class="muted">Redactando…</span>`;
  if (n.work_state === 'error') return `<span class="error">${esc(jparse(n.work_error, {}).message || 'No se pudo redactar')}</span><button class="btn small" onclick="writeNow(${n.id})">Reintentar</button>`;
  return n.editorial_priority && PRIO_LABEL[n.editorial_priority] ? `<span class="muted">En cola para redactar</span>` : '';
}
window.setPriority = async (id, p) => {
  try {
    await api(`/api/candidates/${id}/triage`, { method: 'POST', body: JSON.stringify({ priority: p }) });
    const n = newsCache.find(x => x.id === id);
    if (p === 'no_interest') { newsCache = newsCache.filter(x => x.id !== id); const el = $(`#c-${id}`); if (el) el.remove(); toast('Descartada'); return }
    if (n) { n.editorial_priority = p; if (!n.article_id) n.work_state = 'queued'; $(`#c-${id}`).outerHTML = card(n); if (!n.article_id) poll(id) }
  } catch (e) { toast(e.message) }
};
window.writeNow = async id => {
  const s = $(`#s-${id}`); if (s) s.innerHTML = '<span class="spin"></span><span class="muted">Redactando…</span>';
  try { const r = await api(`/api/candidates/${id}/write?wait=40`, { method: 'POST' }); r.state === 'done' ? done(id, r.article_id) : poll(id) }
  catch (e) { if (s) s.innerHTML = `<span class="error">${esc(e.message)}</span><button class="btn small" onclick="writeNow(${id})">Reintentar</button>` }
};
async function poll(id) {
  if (polling[id]) return; polling[id] = true;
  try {
    for (let i = 0; i < 150; i++) {
      const st = await api(`/api/candidates/${id}/write`);
      const s = $(`#s-${id}`);
      if (st.state === 'done') return done(id, st.article_id);
      if (st.state === 'error') { if (s) s.innerHTML = `<span class="error">${esc(st.error?.detail || st.error?.message || 'No se pudo redactar')}</span><button class="btn small" onclick="writeNow(${id})">Reintentar</button>`; return }
      if (st.state === 'idle') { if (s) s.innerHTML = stateLine({}); return }
      if (s) s.innerHTML = `<span class="spin"></span><span class="muted">${esc(st.step_text || 'Redactando…')}</span>`;
      if (!s && !['news', 'social'].includes(view)) return;
      await wait(4000);
    }
  } catch { } finally { delete polling[id] }
}
function done(id, aid) { const n = newsCache.find(x => x.id === id); if (n) { n.article_id = aid; n.work_state = 'done' } const s = $(`#s-${id}`); if (s) s.innerHTML = stateLine({ article_id: aid, planned_at: n?.planned_at }) }

/* ---------- Redes ---------- */
let socialKind = 'todo', socialPage = 'Todas';
async function socialView(v) {
  const data = await api('/api/social');
  newsCache = data.items;
  const draw = () => {
    const byKind = newsCache.filter(n => socialKind === 'todo' || n.social_type === socialKind);
    const pages = ['Todas', ...new Set(byKind.map(n => n.outlet || n.source_name))];
    if (!pages.includes(socialPage)) socialPage = 'Todas';
    const items = byKind.filter(n => socialPage === 'Todas' || (n.outlet || n.source_name) === socialPage);
    const n = k => newsCache.filter(x => k === 'todo' || x.social_type === k).length;
    v.innerHTML = `<div class="filters"><div class="chips">${[['todo', 'Todo'], ['queja', 'Quejas'], ['propuesta', 'Propuestas'], ['asociacion', 'Asociaciones']].map(([k, t]) => `<button class="chip ${k === socialKind ? 'on' : ''}" data-k="${k}">${t} <b>${n(k)}</b></button>`).join('')}</div>
      <div class="chips scroll">${pages.map(p => `<button class="chip ${p === socialPage ? 'on' : ''}" data-p="${esc(p)}">${esc(p)}</button>`).join('')}</div></div>
      ${items.length ? `<div class="list">${items.map(card).join('')}</div>` : '<p class="empty">No hay publicaciones recientes. Pulsa «Investigar redes».</p>'}
      <section class="panel" style="margin-top:16px"><h2>Páginas y grupos vigilados</h2>
        <p class="muted small">Solo se ve lo público: grupos cerrados no se pueden leer sin permiso de Facebook.</p>
        <div class="sources">${data.watched.map(s => `<div class="srcrow"><label class="switch"><input type="checkbox" ${s.active ? 'checked' : ''} onchange="toggleSource(${s.id})"> ${esc(s.name)}</label><a class="small" href="${safeUrl(s.url)}" target="_blank" rel="noopener">Abrir</a></div>`).join('') || '<p class="muted small">Ninguna todavía.</p>'}</div>
        <form id="addFb" class="stack"><input name="name" placeholder="Nombre (p. ej. Quejas La Línea)" required><input name="url" type="url" placeholder="https://www.facebook.com/groups/…" required><button class="btn small">Vigilar</button></form></section>`;
    v.querySelectorAll('[data-k]').forEach(b => b.onclick = () => { socialKind = b.dataset.k; draw() });
    v.querySelectorAll('[data-p]').forEach(b => b.onclick = () => { socialPage = b.dataset.p; draw() });
    $('#addFb').onsubmit = async e => { e.preventDefault(); const d = Object.fromEntries(new FormData(e.target)); try { await api('/api/sources', { method: 'POST', body: JSON.stringify({ ...d, kind: 'social', priority: 60, local_scope: true }) }); toast('Ahora se vigila'); render() } catch (er) { toast(er.message) } };
    items.forEach(x => { if ((x.work_state === 'queued' || x.work_state === 'working') && !x.article_id) poll(x.id) });
  };
  draw();
}

/* ---------- Agenda ---------- */
async function agenda(v) {
  const s = await api('/api/schedule');
  const groups = {};
  for (const it of s.items) (groups[it.planned_at ? String(it.planned_at).slice(0, 10) : 'sin'] ||= []).push(it);
  v.innerHTML = s.items.length ? Object.entries(groups).map(([day, items]) => `<h2 class="day">${day === 'sin' ? 'Sin hueco' : new Date(day + 'T12:00').toLocaleDateString('es-ES', { weekday: 'long', day: 'numeric', month: 'long' })}</h2>
    <div class="list">${items.map(it => `<article class="item slot"><div class="hour">${it.planned_at ? String(it.planned_at).slice(11, 16) : '–'}</div><div class="grow">
      <div class="meta"><span>${esc(PRIO_LABEL[it.editorial_priority] || '')}</span>${special(it) ? `<span class="flag">${special(it)}</span>` : ''}${it.article_status ? `<span>${STATUS[it.article_status] || ''}</span>` : '<span class="muted">Redactando</span>'}</div>
      <h3>${it.article_id ? `<a href="#editor/${it.article_id}">${esc(it.article_headline || it.title)}</a>` : esc(it.title)}</h3>
      <div class="row"><input type="datetime-local" value="${esc(String(it.planned_at || '').slice(0, 16))}" onchange="setTime(${it.id},'${it.editorial_priority}',this.value)">${it.plan_locked ? `<button class="link" onclick="setPriority(${it.id},'${it.editorial_priority}').then(render)">Automática</button>` : ''}</div>
    </div></article>`).join('')}</div>`).join('')
    : '<p class="empty">Marca noticias como Urgente, Hoy, Esta semana o Futura y aparecerán aquí repartidas por horas.</p>';
}
window.setTime = async (id, p, value) => { if (!value) return; try { await api(`/api/candidates/${id}/triage`, { method: 'POST', body: JSON.stringify({ priority: p, planned_at: value + ':00' }) }); toast('Hora fijada'); render() } catch (e) { toast(e.message) } };

/* ---------- Redacción ---------- */
async function drafts(v) {
  const items = await api('/api/articles');
  v.innerHTML = items.length ? `<div class="list">${items.map(a => `<a class="item art" href="#editor/${a.id}">
      <div class="meta">${secBadge(a.section)}<span class="st st-${a.status}">${STATUS[a.status] || a.status}</span>${a.planned_at ? `<span>${esc(String(a.planned_at).slice(11, 16))} ${isToday(a.planned_at) ? '' : esc(new Date(a.planned_at).toLocaleDateString('es-ES', { day: 'numeric', month: 'short' }))}</span>` : ''}</div>
      <h3>${esc(a.headline)}</h3><div class="meta"><span>${esc(a.outlet || a.source_name || '')}</span>${a.canva_exported ? '<span>Imagen lista</span>' : ''}</div></a>`).join('')}</div>`
    : '<p class="empty">Aún no hay noticias redactadas. Marca una prioridad en Noticias y se redactará sola.</p>';
}

/* ---------- Editor ---------- */
function cw(c) { const W = { ' ': .26, '.': .28, ',': .28, ':': .28, ';': .28, '-': .36, "'": .25 }; if (c in W) return W[c]; if ('iljIJ1!¡íÍ'.includes(c)) return .33; if ('tf'.includes(c)) return .40; if ('mwMW'.includes(c)) return .92; if (c !== c.toLowerCase()) return .72; if (/[0-9]/.test(c)) return .62; return .60 }
function lines(t) { let n = 0, cur = ''; for (const w of String(t || '').split(/\s+/).filter(Boolean)) { const x = (cur + ' ' + w).trim(); if ([...x].reduce((a, c) => a + cw(c), 0) * 74 <= 870 || !cur) cur = x; else { n++; cur = w } } return n + (cur ? 1 : 0) }

async function editor(v) {
  const [a, kit] = await Promise.all([api(`/api/articles/${editorId}`), api(`/api/articles/${editorId}/kit`)]);
  cache.article = a;
  const options = jparse(a.headline_options_json, []), missing = jparse(a.missing_data_json, []), srcs = jparse(a.sources_json, []);
  const published = a.status === 'published';
  v.innerHTML = `<div class="editor">
    <section class="visual">
      <div class="frame" id="frame">${kit.image_url || kit.photo_download_url ? '<img id="preview" alt="Imagen de la noticia">' : '<p class="empty">Sin foto todavía</p>'}</div>
      <div class="row wrap">
        <button class="btn small" onclick="photoPanel()">Cambiar foto</button>
        <button class="btn small" onclick="autoPhoto()">Foto automática</button>
        <button class="btn small" id="canvaBtn" onclick="makeCanva()">${kit.canva_exported ? 'Rehacer imagen en Canva' : 'Crear imagen en Canva'}</button>
        ${kit.image_url ? `<button class="btn small" onclick="download('/media/render/${a.id}.png','infolinense-${a.id}.png')">Descargar imagen</button>` : kit.photo_download_url ? `<button class="btn small" onclick="download('${kit.photo_download_url}','infolinense-${a.id}.jpg')">Descargar foto</button>` : ''}
      </div>
      <p class="muted small">${kit.image_url ? 'Imagen final con la plantilla de Canva.' : kit.photo_download_url ? 'Foto elegida' + (kit.image_credit ? ': ' + esc(kit.image_credit) : '') + '. Crea la imagen en Canva cuando el texto esté listo.' : ''}</p>
      <div id="photos"></div>
    </section>
    <section class="fields">
      <div class="meta"><span class="st st-${a.status}">${STATUS[a.status] || a.status}</span>${a.source_url ? `<a href="${safeUrl(a.source_url)}" target="_blank" rel="noopener noreferrer">Fuente: ${esc(a.outlet || a.source_name || host(a.source_url))}</a>` : ''}${published && a.publish_url ? `<a href="${safeUrl(a.publish_url)}" target="_blank" rel="noopener">Ver en la web</a>` : ''}</div>
      ${a.focus ? `<p class="focus"><b>Enfoque:</b> ${esc(a.focus)}</p>` : ''}
      <label>Sección<select id="f-section">${SECTIONS.map(s => `<option ${s[0] === a.section ? 'selected' : ''}>${s[0]}</option>`).join('')}</select></label>
      <label>Titular<textarea id="f-headline" rows="2">${esc(a.headline)}</textarea></label>
      ${options.length ? `<details class="alts"><summary>7 titulares alternativos</summary>${options.map((h, i) => `<button class="alt" data-i="${i}">${esc(h)}</button>`).join('')}<button class="link" id="moreHeads">Proponer otros 7</button></details>` : `<button class="link" id="moreHeads">Proponer 7 titulares</button>`}
      <label>Titular de la imagen <span class="muted" id="fit"></span><input id="f-image_headline" value="${esc(a.image_headline || '')}" placeholder="Vacío: se usa el titular"></label>
      <label>Entradilla<textarea id="f-subtitle" rows="3">${esc(a.subtitle)}</textarea></label>
      <label>Texto <span class="muted" id="count"></span><textarea id="f-body" rows="16" maxlength="2200">${esc(a.body)}</textarea></label>
      <label>Resumen de la imagen<textarea id="f-graphic_summary" rows="2" maxlength="150">${esc(a.graphic_summary)}</textarea></label>
      <details class="alts" id="carousel"><summary>${a.carousel_suitable ? 'Carrusel recomendado' : 'Carrusel'}</summary><div id="carouselBox"><p class="muted small">${esc(a.carousel_reason || '')}</p><button class="btn small" onclick="carousel()">Preparar diapositivas</button></div></details>
    </section>
  </div>
  <div class="actions">
    <button class="btn" onclick="save(true)">Guardar</button>
    <button class="btn" onclick="copyText()">Copiar</button>
    ${published ? '<span class="muted">Publicada</span>' : `<button class="btn primary" id="pubBtn" onclick="publish()">Publicar</button>`}
  </div>`;
  const f = id => $(`#f-${id}`);
  const fit = () => { const n = lines(f('image_headline').value || f('headline').value); $('#fit').textContent = n <= 4 ? `${n} de 4 líneas` : `${n} líneas: se recortará`; $('#fit').className = n <= 4 ? 'muted' : 'error' };
  const count = () => $('#count').textContent = `${f('body').value.length} / 2.200`;
  f('image_headline').oninput = fit; f('headline').oninput = fit; f('body').oninput = count; fit(); count();
  v.querySelectorAll('.alt').forEach(b => b.onclick = () => { f('headline').value = options[b.dataset.i]; fit(); toast('Titular cambiado') });
  $('#moreHeads').onclick = async e => { e.target.textContent = 'Pensando…'; try { await saveArticle(); await api(`/api/articles/${a.id}/headlines`, { method: 'POST' }); render() } catch (er) { toast(er.message); e.target.textContent = 'Reintentar' } };
  const img = $('#preview');
  if (img) img.src = await blobUrl(kit.image_url || kit.photo_download_url);
  else autoPhoto();
}
function values() { const o = {}; ['section', 'headline', 'image_headline', 'subtitle', 'body', 'graphic_summary'].forEach(k => o[k] = $(`#f-${k}`).value); return o }
async function saveArticle(say) { await api(`/api/articles/${editorId}`, { method: 'PUT', body: JSON.stringify(values()) }); if (say) toast('Guardado') }
window.save = s => saveArticle(s).catch(e => toast(e.message));
window.copyText = async () => { const o = values(); await navigator.clipboard.writeText([o.headline, o.subtitle, o.body].filter(Boolean).join('\n\n')); toast('Texto copiado') };
window.makeCanva = async () => {
  const b = $('#canvaBtn'); b.disabled = true; b.textContent = 'Creando en Canva…';
  try { await saveArticle(); const r = await api(`/api/articles/${editorId}/canva`, { method: 'POST' }); toast(r.exported ? 'Imagen creada' : (r.export_error || 'Diseño creado; falta exportar')); render() }
  catch (e) { toast(e.message); b.disabled = false; b.textContent = 'Crear imagen en Canva' }
};
window.publish = async () => {
  const b = $('#pubBtn'); b.disabled = true; b.textContent = 'Publicando…';
  try { await saveArticle(); const r = await api(`/api/articles/${editorId}/publish`, { method: 'POST' }); toast(r.published ? 'Publicada en la web' : r.message || 'Aprobada'); render() }
  catch (e) { toast(e.message); b.disabled = false; b.textContent = 'Publicar' }
};
window.autoPhoto = async () => {
  const frame = $('#frame'); if (frame) frame.innerHTML = '<p class="muted"><span class="spin"></span> Buscando foto en internet…</p>';
  try { await api(`/api/articles/${editorId}/photo/auto`, { method: 'POST' }); render() }
  catch (e) { if (frame) frame.innerHTML = `<p class="empty">${esc(e.message)}</p>` }
};
window.photoPanel = async (q = '', refresh = false) => {
  const box = $('#photos'); box.innerHTML = '<p class="muted"><span class="spin"></span> Buscando fotos…</p>';
  try {
    const imgs = await api(`/api/articles/${editorId}/photos${q ? '?q=' + encodeURIComponent(q) : refresh ? '?refresh=true' : ''}`);
    cache.photos = imgs;
    box.innerHTML = `<form class="row" id="pq"><input name="q" placeholder="Buscar otra foto" value="${esc(q)}"><button class="btn small">Buscar</button></form>
      <div class="gallery">${imgs.map((im, i) => `<button class="pic" data-i="${i}"><img src="${safeUrl(im.url)}" loading="lazy" referrerpolicy="no-referrer" alt="" onerror="this.parentElement.remove()"><span>${esc(im.source_name || host(im.source))}</span></button>`).join('') || '<p class="muted">Sin resultados.</p>'}</div>
      <div class="row wrap"><button class="link" type="button" id="again">Buscar de nuevo en la noticia</button><label class="link upload">Subir foto<input type="file" accept="image/jpeg,image/png,image/webp" hidden id="up"></label></div>`;
    $('#pq').onsubmit = e => { e.preventDefault(); photoPanel(e.target.q.value) };
    $('#again').onclick = () => photoPanel('', true);
    box.querySelectorAll('.pic').forEach(b => b.onclick = async () => { try { await saveArticle(); await api(`/api/articles/${editorId}/photo`, { method: 'POST', body: JSON.stringify(cache.photos[b.dataset.i]) }); toast('Foto cambiada'); render() } catch (e) { toast(e.message) } });
    $('#up').onchange = async e => { const fd = new FormData(); fd.append('file', e.target.files[0]); try { await saveArticle(); await api(`/api/articles/${editorId}/photo/upload`, { method: 'POST', body: fd }); toast('Foto subida'); render() } catch (er) { toast(er.message) } };
  } catch (e) { box.innerHTML = `<p class="error">${esc(e.message)}</p>` }
};
window.carousel = async () => {
  const box = $('#carouselBox'); box.innerHTML = '<p class="muted"><span class="spin"></span> Preparando…</p>';
  try {
    await saveArticle(); const r = await api(`/api/articles/${editorId}/carousel`, { method: 'POST' });
    box.innerHTML = r.suitable ? `<ol class="slides">${r.slides.map(s => `<li><b>${esc(s.title)}</b><br>${esc(s.text)}</li>`).join('')}</ol>
      <div class="row wrap"><button class="btn small" id="cDesign">Crear diapositivas en Canva</button><button class="link" onclick="carousel()">Rehacer</button></div>`
      : `<p class="muted small">No compensa hacer carrusel: ${esc(r.reason)}</p>`;
    const d = $('#cDesign');
    if (d) d.onclick = async () => { d.disabled = true; d.textContent = 'Creando…'; try { const x = await api(`/api/articles/${editorId}/carousel/design`, { method: 'POST', body: JSON.stringify({ photo_urls: [cache.article.image_url] }) }); toast('Carrusel listo'); download(x.download_url, `infolinense-carrusel-${editorId}.zip`) } catch (e) { toast(e.message) } finally { d.disabled = false; d.textContent = 'Crear diapositivas en Canva' } };
  } catch (e) { box.innerHTML = `<p class="error">${esc(e.message)}</p><button class="btn small" onclick="carousel()">Reintentar</button>` }
};

/* ---------- Ajustes ---------- */
async function settings(v) {
  const [h, caps, srcs] = await Promise.all([api('/api/health'), api('/api/capabilities'), api('/api/sources')]);
  v.innerHTML = `<section class="panel"><h2>Redacción con IA</h2>
      <p>${h.ai_configured ? `Redacta ${h.ai_provider === 'openai' ? 'ChatGPT' : 'Claude'}${h.ai_fallback?.length ? ' (con respaldo)' : ''}.` : 'No hay ninguna clave de IA en Railway.'}</p>
      <button class="btn small" id="aiCheck">Comprobar ahora</button><div id="aiOut"></div></section>
    <section class="panel"><h2>Búsqueda de fotos</h2><p class="muted small">Comprueba qué buscadores de imágenes responden desde el servidor.</p>
      <button class="btn small" id="photoCheck">Comprobar fotos</button><div id="photoOut"></div></section>
    <section class="panel"><h2>Canva</h2><p>${caps.canva_connected ? 'Conectado. Las imágenes usan tu plantilla de noticias.' : caps.canva_configured ? 'Falta autorizar la conexión.' : 'Faltan las credenciales de Canva en Railway.'}</p>
      ${caps.canva_configured ? `<button class="btn small" id="canvaConn">${caps.canva_connected ? 'Volver a conectar' : 'Conectar Canva'}</button>` : ''}</section>
    <section class="panel"><h2>Web</h2><p>${h.auto_publish ? '«Aprobar y publicar» envía la noticia a infolinense.com.' : 'La publicación en infolinense.com no está configurada: «Aprobar y publicar» solo aprueba.'}</p></section>
    <section class="panel"><h2>Fuentes</h2>
      <div class="sources">${srcs.map(s => `<div class="srcrow"><label class="switch"><input type="checkbox" ${s.active ? 'checked' : ''} onchange="toggleSource(${s.id})"> ${esc(s.name)}</label>${s.last_error ? `<span class="error small">Error</span>` : `<span class="muted small">${s.items_added ?? 0} nuevas</span>`}</div>`).join('')}</div>
      <form id="addSrc" class="stack"><input name="name" placeholder="Nombre del medio o página" required><input name="url" type="url" placeholder="https://… (RSS, portada, Facebook o Instagram)" required>
      <select name="kind"><option value="rss">RSS</option><option value="html">Portada web</option><option value="social">Facebook / Instagram</option></select><button class="btn small">Añadir fuente</button></form></section>
    <button class="btn" onclick="logout()">Salir</button>`;
  $('#aiCheck').onclick = async e => {
    e.target.disabled = true; e.target.textContent = 'Comprobando…';
    try { const r = await api('/api/ai/check'); $('#aiOut').innerHTML = r.providers.filter(p => p.configured).map(p => `<p class="${p.ok ? 'ok' : 'error'}"><b>${esc(p.provider_name)}</b> ${p.active ? '(principal)' : '(respaldo)'}: ${p.ok ? 'funciona' : esc(p.message) + (p.http_status ? ` <small>(HTTP ${p.http_status} ${esc(p.code || '')})</small>` : '')}</p>`).join('') || '<p class="error">Sin claves configuradas.</p>' }
    catch (er) { $('#aiOut').innerHTML = `<p class="error">${esc(er.message)}</p>` } finally { e.target.disabled = false; e.target.textContent = 'Comprobar ahora' }
  };
  $('#photoCheck').onclick = async e => {
    e.target.disabled = true; e.target.textContent = 'Comprobando…';
    try { const r = await api('/api/photos/check'); $('#photoOut').innerHTML = r.providers.map(p => `<p class="${p.count ? 'ok' : p.count === null ? 'muted' : 'error'}"><b>${esc(p.name)}</b>: ${p.count === null ? 'sin configurar' : p.count + ' fotos'}</p>`).join('') }
    catch (er) { $('#photoOut').innerHTML = `<p class="error">${esc(er.message)}</p>` } finally { e.target.disabled = false; e.target.textContent = 'Comprobar fotos' }
  };
  const cc = $('#canvaConn'); if (cc) cc.onclick = async () => { try { location.href = (await api('/api/canva/connect')).url } catch (e) { toast(e.message) } };
  $('#addSrc').onsubmit = async e => { e.preventDefault(); const d = Object.fromEntries(new FormData(e.target)); try { await api('/api/sources', { method: 'POST', body: JSON.stringify({ ...d, priority: 60 }) }); toast('Fuente añadida'); render() } catch (er) { toast(er.message) } };
}
window.toggleSource = async id => { try { await api(`/api/sources/${id}/toggle`, { method: 'POST' }) } catch (e) { toast(e.message) } };
window.go = go; window.download = download; window.logout = logout; window.render = render;

if (token) start(); else logout();
