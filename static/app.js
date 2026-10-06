/* InfoLinense Desk 4 — trabajo por fases: 1 Ordenar · 2 Redacción · 3 Revisar · 4 Publicar. */
const $ = s => document.querySelector(s);
let token = localStorage.getItem('infolinense_token') || '';
let view = 'sort', editorId = null, block = null, editorFrom = 'review';
let blobUrls = [];
const polling = {};
const cache = { photos: {}, article: {} };

const PRIO = [['urgent', 'Urgente'], ['today', 'Hoy'], ['this_week', 'Esta semana'], ['future', 'Más adelante']];
const PRIO_LABEL = Object.fromEntries(PRIO);
const BLOCKS = ['Ayuntamiento', 'Licitaciones y edictos', 'Otros medios', 'Nacionales adaptables', 'Redes sociales'];
const SECTIONS = [['OBRAS', '#0150FE', '#fff'], ['CIUDAD', '#4F9AFC', '#fff'], ['GIBRALTAR', '#7249E0', '#fff'], ['SUCESOS', '#D02132', '#fff'],
  ['CULTURA', '#E72E79', '#fff'], ['DEPORTES', '#00AB4F', '#fff'], ['COMERCIO', '#FF8E1A', '#fff'], ['MEDIO AMBIENTE', '#62DBD1', '#061E5C'],
  ['POLÍTICA', '#08176E', '#fff'], ['SOCIEDAD', '#2756CD', '#fff'], ['PATRIMONIO', '#B9831E', '#fff'], ['AGENDA', '#FDE206', '#061E5C']];
const SEC = Object.fromEntries(SECTIONS.map(s => [s[0], s]));
const STATUS = { draft: 'Borrador', review_ready: 'Revisada', approved: 'Aprobada', published: 'Publicada' };
const PHASES = [['sort', 'Ordenar'], ['writing', 'Redacción'], ['review', 'Revisar'], ['publish', 'Publicar']];
const KIND = { queja: 'Queja', propuesta: 'Propuesta', asociacion: 'Asociación' };

/* ---------- utilidades ---------- */
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m])) }
function safeUrl(s) { try { const u = new URL(s); return ['http:', 'https:'].includes(u.protocol) ? esc(u.href) : '#' } catch { return '#' } }
function jparse(s, d) { try { return JSON.parse(s || '') ?? d } catch { return d } }
function host(u) { try { return new URL(u).hostname.replace('www.', '') } catch { return '' } }
function toast(t) { const el = $('#toast'); el.textContent = t; el.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => el.hidden = true, 3200) }
function ago(iso) {
  if (!iso) return '';
  const d = new Date(iso), now = new Date(), min = Math.round((now - d) / 60000);
  if (/T09:00:00(\.000)?(\+00:00|Z)$/.test(iso)) {  // solo se conoce el día (edictos, licitaciones, boletines)
    return isToday(iso) ? 'hoy' : d.toLocaleDateString('es-ES', { timeZone: 'Europe/Madrid', day: 'numeric', month: 'short', year: d.getFullYear() !== now.getFullYear() ? 'numeric' : undefined });
  }
  if (min < 60) return `hace ${Math.max(1, min)} min`;
  if (min < 600) return `hace ${Math.round(min / 60)} h`;
  const day = d.toLocaleDateString('es-ES', { timeZone: 'Europe/Madrid' }) === now.toLocaleDateString('es-ES', { timeZone: 'Europe/Madrid' }) ? 'hoy'
    : d.toLocaleDateString('es-ES', { timeZone: 'Europe/Madrid', day: 'numeric', month: 'short' });
  return `${day} ${d.toLocaleTimeString('es-ES', { timeZone: 'Europe/Madrid', hour: '2-digit', minute: '2-digit' })}`;
}
function isToday(iso) { return iso && new Date(iso).toLocaleDateString('es-ES', { timeZone: 'Europe/Madrid' }) === new Date().toLocaleDateString('es-ES', { timeZone: 'Europe/Madrid' }) }
function secBadge(name) { const s = SEC[name] || SEC.CIUDAD; return `<span class="sec" style="background:${s[1]};color:${s[2]}">${esc(s[0])}</span>` }
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
async function start() {
  $('#login').hidden = true; $('#app').hidden = false;
  const h = location.hash.slice(1);
  if (h) return go(h);
  // Al abrir, se va a la primera fase con trabajo pendiente.
  try { const w = await api('/api/workflow'); go(w.sort ? 'sort' : w.review ? 'review' : w.drafting ? 'writing' : w.publish ? 'publish' : 'sort') } catch { go('sort') }
}
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => go(b.dataset.view));
$('#settingsBtn').onclick = () => go('settings');
$('#backBtn').onclick = () => go(editorFrom);
window.onhashchange = () => { const h = location.hash.slice(1); if (h && h !== currentHash()) go(h, true) };
function currentHash() { return view === 'editor' ? `editor/${editorId}` : view }
function go(target, fromHash) {
  const [v, id] = target.split('/');
  if (view !== 'editor' && v === 'editor') editorFrom = view === 'settings' ? 'review' : view;
  view = ['sort', 'writing', 'review', 'publish', 'settings', 'editor'].includes(v) ? v : 'sort';
  editorId = view === 'editor' ? Number(id) : null;
  if (!fromHash) location.hash = currentHash();
  document.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('on', b.dataset.view === (view === 'editor' ? editorFrom : view)));
  $('#title').textContent = { sort: 'Ordenar', writing: 'Redacción', review: 'Revisar', publish: 'Publicar', settings: 'Ajustes', editor: 'Noticia' }[view];
  $('#backBtn').hidden = view !== 'editor';
  $('#scanBtn').hidden = view !== 'sort';
  render(); counts();
}
async function counts() {
  try {
    const w = await api('/api/workflow');
    const n = { sort: w.sort, writing: w.drafting, review: w.review, publish: w.publish };
    document.querySelectorAll('.tabs button').forEach(b => { const c = b.querySelector('.n'); if (c) c.textContent = n[b.dataset.view] || '' });
  } catch { }
}
async function render() {
  blobUrls.forEach(URL.revokeObjectURL); blobUrls = [];
  const v = $('#view'); v.innerHTML = '<p class="empty">Cargando…</p>'; window.scrollTo(0, 0);
  try { await ({ sort: sortView, writing: writingView, review: reviewView, publish: publishView, settings, editor }[view])(v) }
  catch (e) { if (token) v.innerHTML = `<p class="empty">${esc(e.message)}</p>` }
}
function nextPhase(label, target, note = '') { return `<div class="next"><p>${note}</p><button class="btn primary" onclick="go('${target}')">${label}</button></div>` }

/* ---------- Fase 1: Ordenar ---------- */
$('#scanBtn').onclick = async () => {
  const b = $('#scanBtn'); b.disabled = true; b.textContent = 'Buscando…';
  try {
    const [r, sr] = await Promise.all([api('/api/scan', { method: 'POST' }), api('/api/social/scan', { method: 'POST' }).catch(() => ({ added: [] }))]);
    toast(r.busy ? 'Ya hay una búsqueda en marcha' : `${r.added.length + sr.added.length} nuevas`); render(); counts();
  } catch (e) { toast(e.message) } finally { b.disabled = false; b.textContent = 'Buscar' }
};
let queue = [];
async function sortView(v) {
  queue = await api('/api/sort-queue');
  drawSort(v);
}
function drawSort(v = $('#view')) {
  const count = g => queue.filter(n => n.group === g).length;
  if (!block || !count(block)) block = BLOCKS.find(count) || BLOCKS[0];
  const items = queue.filter(n => n.group === block);
  const n = items[0];
  v.innerHTML = `<div class="chips scroll blocks">${BLOCKS.map(g => `<button class="chip ${g === block ? 'on' : ''}" data-g="${esc(g)}" ${count(g) ? '' : 'disabled'}>${esc(g)} <b>${count(g)}</b></button>`).join('')}</div>
    ${n ? `<p class="progress">${esc(block)}: quedan ${items.length}</p>${focusCard(n)}
      ${items.length > 1 ? `<details class="peek"><summary>Ver las ${items.length - 1} siguientes</summary>${items.slice(1, 15).map(x => `<p class="peeki">${esc(x.title)}</p>`).join('')}</details>` : ''}`
    : queue.length ? '' : `<p class="empty">Todo ordenado. Pulsa «Buscar» para traer noticias nuevas.</p>${nextPhase('Ir a Redacción', 'writing', 'Las noticias marcadas se están redactando solas.')}`}`;
  v.querySelectorAll('[data-g]').forEach(b => b.onclick = () => { block = b.dataset.g; drawSort() });
}
function special(n) { const t = `${n.title} ${n.excerpt || ''} ${n.source_name || ''}`.toLowerCase(); if (n.source_kind === 'procurement' || /licitaci|contrataci|adjudicaci/.test(t)) return 'Licitación'; if (n.source_kind === 'edictos' || n.source_kind === 'bop' || /edicto/.test(t)) return 'Edicto'; if (/exclusiv/.test(t)) return 'Exclusiva'; return '' }
function focusCard(n) {
  const sp = special(n);
  return `<article class="focuscard" id="fc">
    <div class="meta">${n.social_type ? `<span class="kind ${n.social_type}">${KIND[n.social_type] || ''}</span>` : ''}${sp ? `<span class="flag">${sp}</span>` : ''}<span class="src">${esc(n.outlet || n.source_name || host(n.url))}${(n.links || []).length ? ` +${n.links.length} ${n.links.length === 1 ? 'fuente' : 'fuentes'}` : ''}</span><span>${n.date_iso ? ago(n.date_iso) : 'fecha no indicada'}</span></div>
    <h2>${esc(n.title)}</h2>
    ${n.local_angle ? `<p class="angle">${esc(n.local_angle)}</p>` : n.excerpt && n.excerpt !== n.title ? `<p class="angle">${esc(n.excerpt.slice(0, 260))}</p>` : ''}
    <a class="link" href="${safeUrl(n.url)}" target="_blank" rel="noopener noreferrer">Ver la fuente</a>
    ${(n.links || []).length ? `<p class="muted small">También la cuentan: ${n.links.map(l => `<a href="${safeUrl(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.outlet || l.source_name || host(l.url))}</a>`).join(' · ')}. Se combinan al redactar.</p>` : ''}
    <div class="decide">${PRIO.map(([k, t]) => `<button class="btn ${k === 'urgent' ? 'urgent' : k === 'today' ? 'primary' : ''}" onclick="decide(${n.id},'${k}')">${t}</button>`).join('')}
      <button class="btn no" onclick="decide(${n.id},'no_interest')">No interesa</button></div></article>`;
}
window.decide = async (id, p) => {
  const card = $('#fc'); if (card) card.classList.add('gone');
  try {
    await api(`/api/candidates/${id}/triage`, { method: 'POST', body: JSON.stringify({ priority: p }) });
    queue = queue.filter(x => x.id !== id);
    if (p !== 'no_interest') toast(`${PRIO_LABEL[p]}: se redacta sola`);
    drawSort(); counts();
  } catch (e) { toast(e.message); if (card) card.classList.remove('gone') }
};

/* ---------- Fase 2: Redacción ---------- */
async function writingView(v) {
  const rows = await api('/api/drafting');
  const ready = rows.filter(r => r.state === 'ready').length;
  v.innerHTML = rows.length ? `<div class="bar"><span style="width:${Math.round(100 * ready / rows.length)}%"></span></div>
    <p class="progress">${ready} de ${rows.length} listas para revisar</p>
    <div class="list">${rows.map(r => `<article class="item row-${r.state}"><div class="meta"><span>${esc(PRIO_LABEL[r.editorial_priority] || '')}</span>${r.planned_at ? `<span>${esc(String(r.planned_at).slice(11, 16))}</span>` : ''}</div>
      <h3>${esc(r.headline || r.title)}</h3>
      <div class="state">${r.state === 'ready' ? `<button class="btn small primary" onclick="go('editor/${r.article_id}')">Revisar</button>` :
        r.state === 'error' ? `<span class="error">${esc(jparse(r.work_error, {}).message || 'No se pudo redactar')}</span><button class="btn small" onclick="retry(${r.id})">Reintentar</button>` :
        `<span class="spin"></span><span class="muted">${r.state === 'working' ? 'Redactando…' : 'En cola'}</span>`}</div></article>`).join('')}</div>
    ${ready ? nextPhase('Pasar a Revisar', 'review') : ''}`
    : `<p class="empty">No hay nada redactándose. Marca noticias en Ordenar.</p>${nextPhase('Ir a Ordenar', 'sort')}`;
  if (rows.some(r => r.state === 'queued' || r.state === 'working')) setTimeout(() => { if (view === 'writing') { writingView(v); counts() } }, 6000);
}
window.retry = async id => { try { await api(`/api/candidates/${id}/write?wait=0`, { method: 'POST' }); render() } catch (e) { toast(e.message) } };

/* ---------- Fase 3: Revisar ---------- */
let reviewIds = [];
async function reviewView(v) {
  const rows = (await api('/api/drafting')).filter(r => r.state === 'ready');
  reviewIds = rows.map(r => r.article_id);
  v.innerHTML = rows.length ? `<p class="progress">${rows.length} por revisar. Corrige texto, foto e imagen y pulsa «Revisada».</p>
    <div class="list">${rows.map(r => `<a class="item" href="#editor/${r.article_id}"><div class="meta"><span>${esc(PRIO_LABEL[r.editorial_priority] || '')}</span>${r.planned_at ? `<span>${esc(String(r.planned_at).slice(11, 16))}</span>` : ''}</div><h3>${esc(r.headline || r.title)}</h3></a>`).join('')}</div>`
    : `<p class="empty">No queda nada por revisar.</p>${nextPhase('Ir a Publicar', 'publish')}`;
}

/* ---------- Fase 4: Publicar ---------- */
const NET_NAMES = { web: 'Web', instagram: 'Instagram', facebook: 'Facebook', tiktok: 'TikTok' };
function netPrefs(available) { let saved = null; try { saved = JSON.parse(localStorage.getItem('nets') || 'null') } catch (e) { } return available.filter(n => !saved || saved.includes(n)) }
function saveNetPrefs() { try { localStorage.setItem('nets', JSON.stringify([...document.querySelectorAll('.netpick input:checked')].map(i => i.value))) } catch (e) { } }
function netStatus(r) { const n = r.networks || {}; return Object.keys(n).map(k => n[k].status === 'published' ? (n[k].url ? `<a class="net ok" href="${safeUrl(n[k].url)}" target="_blank" rel="noopener">${NET_NAMES[k]} ✓</a>` : `<span class="net ok">${NET_NAMES[k]} ✓</span>`) : `<button class="net bad" title="${esc(n[k].message)}" onclick="retryNet(${r.id},'${k}',this)">${NET_NAMES[k]}: reintentar</button>`).join('') }
async function publishView(v) {
  const [rows, nets] = await Promise.all([api('/api/to-publish'), api('/api/networks').catch(() => ({}))]);
  const available = ['instagram', 'facebook', 'tiktok'].filter(n => (nets[n] || {}).connected);
  const chosen = netPrefs(available);
  const picker = available.length ? `<div class="netpick row wrap"><span class="muted small">Publicar también en:</span>${available.map(n => `<label class="switch"><input type="checkbox" value="${n}" ${chosen.includes(n) ? 'checked' : ''} onchange="saveNetPrefs()"> ${NET_NAMES[n]}</label>`).join('')}</div>` : `<p class="muted small">Las redes sociales no están conectadas. <a href="#settings">Conéctalas en Ajustes</a>.</p>`;
  const today = new Date().toLocaleDateString('sv-SE', { timeZone: 'Europe/Madrid' });
  const isTodayRow = r => !r.planned_at || String(r.planned_at).slice(0, 10) <= today;
  const row = r => `<article class="item slot"><div class="hour">${r.planned_at ? esc(String(r.planned_at).slice(11, 16)) : '–'}</div><div class="grow">
      <div class="meta">${secBadge(r.section)}<span class="st st-${r.status}">${STATUS[r.status]}</span><span>${r.canva_exported ? 'Imagen lista' : r.has_photo ? 'Con foto' : 'Sin foto'}</span></div>
      <h3><a href="#editor/${r.id}">${esc(r.headline)}</a></h3>
      ${r.status === 'published' ? `<div class="row wrap">${r.publish_url ? `<a class="net ok" href="${safeUrl(r.publish_url)}" target="_blank" rel="noopener">Web ✓</a>` : ''}${netStatus(r)}${r.canva_exported ? `<button class="net" onclick="shareFb(${r.id},this)">Compartir en Facebook</button>` : ''}</div>` :
        `<div class="row wrap"><input type="datetime-local" value="${esc(String(r.planned_at || '').slice(0, 16))}" onchange="setTime(${r.candidate_id},'${r.editorial_priority || 'today'}',this.value)">
         <button class="btn small primary" onclick="publishNow(${r.id},this)">Publicar</button>${r.canva_exported ? `<button class="btn small" onclick="shareFb(${r.id},this)">Compartir en Facebook</button>` : ''}</div>`}</div></article>`;
  const todayRows = rows.filter(isTodayRow), later = rows.filter(r => !isTodayRow(r));
  v.innerHTML = rows.length ? `${picker}<h2 class="day">Hoy</h2><div class="list">${todayRows.map(row).join('') || '<p class="muted">Nada para hoy.</p>'}</div>
    ${later.length ? `<h2 class="day">Próximos días</h2><div class="list">${later.map(row).join('')}</div>` : ''}`
    : `<p class="empty">Aún no hay noticias revisadas.</p>${nextPhase('Ir a Revisar', 'review')}`;
}
window.setTime = async (id, p, value) => { if (!value) return; try { await api(`/api/candidates/${id}/triage`, { method: 'POST', body: JSON.stringify({ priority: p, planned_at: value + ':00' }) }); toast('Hora fijada'); render() } catch (e) { toast(e.message) } };
function pickedNets() { const boxes = document.querySelectorAll('.netpick input'); if (boxes.length) return [...boxes].filter(i => i.checked).map(i => i.value); let saved = null; try { saved = JSON.parse(localStorage.getItem('nets') || 'null') } catch (e) { } return saved }
function publishReport(r) { const lines = (r.results || []).filter(x => x.message).map(x => (x.ok ? '✓ ' : '✗ ') + x.message); toast(lines.join(' · ') || (r.published ? 'Publicada' : r.message || 'Aprobada')) }
window.publishNow = async (id, b) => { b.disabled = true; b.textContent = 'Publicando…'; try { const r = await api(`/api/articles/${id}/publish`, { method: 'POST', body: JSON.stringify({ networks: pickedNets() }) }); publishReport(r); render(); counts() } catch (e) { toast(e.message); b.disabled = false; b.textContent = 'Publicar' } };
// Facebook personal: Meta no deja publicar en perfiles por programa, así que se comparte a mano.
// 1.ª pulsación: prepara imagen y texto. 2.ª: abre el menú «Compartir» del móvil (o descarga la imagen y abre Facebook).
const shareKits = {};
window.shareFb = async (id, b) => {
  const kit = shareKits[id];
  if (!kit) {
    b.disabled = true; const label = b.textContent; b.textContent = 'Preparando…';
    try {
      const k = await api(`/api/articles/${id}/share`);
      const files = [];
      for (const [i, u] of k.images.entries()) { const r = await fetch(u); if (r.ok) files.push(new File([await r.blob()], `infolinense-${id}-${i + 1}.jpg`, { type: 'image/jpeg' })) }
      shareKits[id] = { text: k.text + (k.link ? '\n\n' + k.link : ''), files };
      b.textContent = 'Compartir ahora'; toast('Listo: pulsa «Compartir ahora»');
    } catch (e) { toast(e.message); b.textContent = label } finally { b.disabled = false }
    return;
  }
  const copied = navigator.clipboard ? navigator.clipboard.writeText(kit.text).then(() => true, () => false) : Promise.resolve(false);
  if (kit.files.length && navigator.canShare && navigator.canShare({ files: kit.files })) {
    try { await navigator.share({ files: kit.files, text: kit.text }); toast((await copied) ? 'Texto copiado: si Facebook no lo pone, mantén pulsado y «Pegar»' : 'Compartido'); return }
    catch (e) { if (e.name === 'AbortError') return }
  }
  for (const f of kit.files) { const a = document.createElement('a'); a.href = URL.createObjectURL(f); a.download = f.name; document.body.appendChild(a); a.click(); a.remove() }
  window.open('https://www.facebook.com/', '_blank', 'noopener');
  toast((await copied) ? 'Imagen descargada y texto copiado. En Facebook: «¿Qué estás pensando?» → pega el texto y añade la foto' : 'Imagen descargada. Copia el texto con el botón «Copiar»');
};
window.retryNet = async (id, net, b) => { b.disabled = true; b.textContent = 'Publicando…'; try { const r = await api(`/api/articles/${id}/networks/${net}`, { method: 'POST' }); toast((r.ok ? '✓ ' : '✗ ') + r.message); render() } catch (e) { toast(e.message); b.disabled = false } };
window.saveNetPrefs = saveNetPrefs;

/* ---------- Editor ---------- */
function cw(c) { const W = { ' ': .26, '.': .28, ',': .28, ':': .28, ';': .28, '-': .36, "'": .25 }; if (c in W) return W[c]; if ('iljIJ1!¡íÍ'.includes(c)) return .33; if ('tf'.includes(c)) return .40; if ('mwMW'.includes(c)) return .92; if (c !== c.toLowerCase()) return .72; if (/[0-9]/.test(c)) return .62; return .60 }
function lines(t) { let n = 0, cur = ''; for (const w of String(t || '').split(/\s+/).filter(Boolean)) { const x = (cur + ' ' + w).trim(); if ([...x].reduce((a, c) => a + cw(c), 0) * 74 <= 870 || !cur) cur = x; else { n++; cur = w } } return n + (cur ? 1 : 0) }

async function editor(v) {
  const [a, kit] = await Promise.all([api(`/api/articles/${editorId}`), api(`/api/articles/${editorId}/kit`)]);
  cache.article = a; cache.kit = kit;
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
      <label>Titular <span class="muted" id="fit"></span><textarea id="f-headline" rows="2">${esc(a.headline)}</textarea></label>
      ${options.length ? `<details class="alts"><summary>7 titulares alternativos</summary>${options.map((h, i) => `<button class="alt" data-i="${i}">${esc(h)}</button>`).join('')}<button class="link" id="moreHeads">Proponer otros 7</button></details>` : `<button class="link" id="moreHeads">Proponer 7 titulares</button>`}
      <label>Entradilla <span class="muted" id="sub"></span><textarea id="f-subtitle" rows="3">${esc(a.subtitle)}</textarea></label>
      <p class="muted small">La imagen de Canva lleva exactamente este titular y esta entradilla.</p>
      <label>Texto <span class="muted" id="count"></span><textarea id="f-body" rows="16" maxlength="2200">${esc(a.body)}</textarea></label>
      <details class="alts" id="carousel"><summary>${a.carousel_suitable ? 'Carrusel recomendado' : 'Carrusel'}</summary><div id="carouselBox"><p class="muted small">${esc(a.carousel_reason || '')}</p><button class="btn small" onclick="carousel()">Preparar diapositivas</button></div></details>
    </section>
  </div>
  <div class="actions">
    <button class="btn" onclick="save(true)">Guardar</button>
    <button class="btn" onclick="copyText()">Copiar</button>
    ${a.status !== 'draft' ? `<button class="btn" onclick="shareFb(${a.id},this)">Compartir en Facebook</button>` : ''}
    ${published ? '<span class="muted">Publicada</span>' : a.status === 'draft' ? `<button class="btn primary" onclick="reviewed()">Revisada</button>` : `<button class="btn primary" id="pubBtn" onclick="publish()">Publicar</button>`}
  </div>`;
  const f = id => $(`#f-${id}`);
  const fit = () => { const n = lines(f('headline').value); $('#fit').textContent = n <= 4 ? `${n} de 4 líneas en la imagen` : `${n} líneas: no cabe en la imagen, acórtalo`; $('#fit').className = n <= 4 ? 'muted' : 'error' };
  const sub = () => { const n = f('subtitle').value.trim().length; $('#sub').textContent = n <= 165 ? `${n} / 165` : `${n} / 165: no cabe en la imagen, acórtala`; $('#sub').className = n <= 165 ? 'muted' : 'error' };
  const count = () => $('#count').textContent = `${f('body').value.length} / 2.200`;
  f('headline').oninput = fit; f('subtitle').oninput = sub; f('body').oninput = count; fit(); sub(); count();
  v.querySelectorAll('.alt').forEach(b => b.onclick = () => { f('headline').value = options[b.dataset.i]; fit(); toast('Titular cambiado') });
  $('#moreHeads').onclick = async e => { e.target.textContent = 'Pensando…'; try { await saveArticle(); await api(`/api/articles/${a.id}/headlines`, { method: 'POST' }); render() } catch (er) { toast(er.message); e.target.textContent = 'Reintentar' } };
  const img = $('#preview');
  if (img) img.src = await blobUrl(kit.image_url || kit.photo_download_url);
  else autoPhoto();
}
function values() { const o = {}; ['section', 'headline', 'subtitle', 'body'].forEach(k => o[k] = $(`#f-${k}`).value); return o }
async function saveArticle(say) { await api(`/api/articles/${editorId}`, { method: 'PUT', body: JSON.stringify(values()) }); if (say) toast('Guardado') }
window.save = s => saveArticle(s).catch(e => toast(e.message));
window.copyText = async () => { const o = values(); await navigator.clipboard.writeText([o.headline, o.subtitle, o.body].filter(Boolean).join('\n\n')); toast('Texto copiado') };
window.makeCanva = async () => {
  const b = $('#canvaBtn'); b.disabled = true; b.textContent = 'Creando en Canva…';
  try { await saveArticle(); const r = await api(`/api/articles/${editorId}/canva`, { method: 'POST' }); toast(r.exported ? 'Imagen creada' : (r.export_error || 'Diseño creado; falta exportar')); render() }
  catch (e) { toast(e.message); b.disabled = false; b.textContent = 'Crear imagen en Canva' }
};
window.reviewed = async () => {
  try {
    await saveArticle(); await api(`/api/articles/${editorId}/ready`, { method: 'POST' });
    const next = reviewIds.filter(x => x !== editorId)[0];
    reviewIds = reviewIds.filter(x => x !== editorId);
    toast('Revisada: pasa a Publicar'); counts();
    if (next) go('editor/' + next); else go('publish');
  } catch (e) { toast(e.message) }
};
window.publish = async () => {
  const b = $('#pubBtn'); b.disabled = true; b.textContent = 'Publicando…';
  try { await saveArticle(); const r = await api(`/api/articles/${editorId}/publish`, { method: 'POST', body: JSON.stringify({ networks: pickedNets() }) }); publishReport(r); render() }
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
    const query = q || cache.article.photo_query || (cache.kit || {}).photo_query || '';
    const gUrl = 'https://www.google.com/search?tbm=isch&hl=es&q=' + encodeURIComponent(query);
    box.innerHTML = `<form class="row" id="pq"><input name="q" placeholder="Escribe como en Google Imágenes" value="${esc(query)}"><button class="btn small">Buscar</button></form>
      <p class="muted small">¿No te gusta ninguna? <a href="${gUrl}" target="_blank" rel="noopener" id="gLink">Abrir en Google Imágenes</a>, pulsa la foto, «Copiar dirección de la imagen» y pégala aquí:</p>
      <form class="row" id="pu"><input name="u" type="url" placeholder="Pega aquí el enlace de la foto" inputmode="url"><button class="btn small">Usar</button></form>
      <div class="gallery">${imgs.map((im, i) => `<button class="pic" data-i="${i}"><img src="${safeUrl(im.url)}" loading="lazy" referrerpolicy="no-referrer" alt="" onerror="this.parentElement.remove()"><span>${esc(im.source_name || host(im.source))}</span></button>`).join('') || '<p class="muted">Ningún buscador devolvió fotos. Prueba con otras palabras o usa «Abrir en Google Imágenes».</p>'}</div>
      <div class="row wrap"><button class="link" type="button" id="again">Buscar de nuevo en la noticia</button><label class="link upload">Subir foto<input type="file" accept="image/jpeg,image/png,image/webp" hidden id="up"></label></div>`;
    $('#pq').onsubmit = e => { e.preventDefault(); photoPanel(e.target.q.value) };
    $('#pq').q.oninput = e => { $('#gLink').href = 'https://www.google.com/search?tbm=isch&hl=es&q=' + encodeURIComponent(e.target.value) };
    $('#pu').onsubmit = async e => { e.preventDefault(); const u = e.target.u.value.trim(); if (!u) return; try { await saveArticle(); await api(`/api/articles/${editorId}/photo`, { method: 'POST', body: JSON.stringify({ url: u, source: u }) }); toast('Foto cambiada'); render() } catch (er) { toast(er.message) } };
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
  const [h, caps, srcs, nets] = await Promise.all([api('/api/health'), api('/api/capabilities'), api('/api/sources'), api('/api/networks').catch(() => ({}))]);
  v.innerHTML = `<section class="panel"><h2>Redacción con IA</h2>
      <p>${h.ai_configured ? `Redacta ${{ openai: 'ChatGPT', anthropic: 'Claude', gemini: 'Gemini (gratis)' }[h.ai_provider] || h.ai_provider}.` : 'Sin IA: los borradores se preparan a partir de la fuente. Añade GEMINI_API_KEY (gratis) en Railway.'}</p>
      <p class="muted small">${h.paid_ai_allowed ? 'Las IA de pago están permitidas.' : 'Modo sin coste: ChatGPT y Claude están desactivados aunque tengan clave.'}</p>
      <button class="btn small" id="aiCheck">Comprobar ahora</button><div id="aiOut"></div></section>
    <section class="panel"><h2>Búsqueda de fotos</h2><p class="muted small">Comprueba qué buscadores de imágenes responden desde el servidor.</p>
      <button class="btn small" id="photoCheck">Comprobar fotos</button><div id="photoOut"></div></section>
    <section class="panel"><h2>Canva</h2><p>${caps.canva_connected ? 'Conectado. Las imágenes usan tu plantilla de noticias.' : caps.canva_configured ? 'Falta autorizar la conexión.' : 'Faltan las credenciales de Canva en Railway.'}</p>
      ${caps.canva_configured ? `<button class="btn small" id="canvaConn">${caps.canva_connected ? 'Volver a conectar' : 'Conectar Canva'}</button>` : ''}</section>
    <section class="panel"><h2>Redes sociales</h2>
      <p class="muted small">Solo se publica cuando pulsas «Publicar». Se usa la imagen de Canva (o el carrusel, si lo has creado) y el texto de la noticia.</p>
      <div class="netrow"><b>Instagram</b>
        <p>${nets.instagram && nets.instagram.connected ? 'Conectado: @' + esc(nets.instagram.name || 'cuenta') + '. Se publica automático al pulsar «Publicar».' : nets.instagram_login ? 'Sin conectar.' : 'Faltan INSTAGRAM_APP_ID e INSTAGRAM_APP_SECRET en Railway.'}</p>
        ${nets.instagram_login ? `<div class="row wrap"><button class="btn small" id="igConn">${nets.instagram && nets.instagram.direct ? 'Volver a conectar' : 'Conectar Instagram'}</button>${nets.instagram && nets.instagram.direct ? '<button class="link" id="igOff">Desconectar</button>' : ''}</div>` : ''}
        ${nets.instagram_callback ? `<p class="muted small">Dirección de retorno para «Instagram → Configuración de la API con inicio de sesión de Instagram»: <code>${esc(nets.instagram_callback)}</code></p>` : ''}</div>
      <div class="netrow"><b>Facebook</b>
        ${nets.facebook && nets.facebook.connected ? `<p>Página: ${esc(nets.facebook.name || 'conectada')}. Se publica automático.</p><button class="link" id="metaOff">Desconectar página</button>` : '<p>Tu cuenta personal no se toca. Meta no permite publicar en perfiles personales de forma automática, así que en cada noticia tienes el botón <b>«Compartir en Facebook»</b>: deja la imagen y el texto listos y solo tienes que pulsar «Publicar» en Facebook.</p>'}
        ${(nets.pages || []).length > 1 ? `<label>Página<select id="pagePick">${nets.pages.map(p => `<option value="${esc(p.id)}" ${p.name === nets.facebook.name ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select></label>` : ''}
        ${nets.meta_login && !(nets.facebook && nets.facebook.connected) ? `<details class="alts"><summary>Solo si algún día creas una página de Facebook</summary><button class="btn small" id="metaConn">Conectar página de Facebook</button>
          <form id="metaTok" class="stack"><input name="token" placeholder="O pega el token del Explorador de la API Graph" autocomplete="off"><button class="btn small">Conectar con este token</button></form></details>` : ''}</div>
      <div class="netrow"><b>TikTok</b>
        <p>${nets.tiktok && nets.tiktok.connected ? 'Conectado' + (nets.tiktok.name ? ': ' + esc(nets.tiktok.name) : '') + '.' : nets.tiktok_configured ? 'Sin conectar.' : 'Faltan TIKTOK_CLIENT_KEY y TIKTOK_CLIENT_SECRET en Railway.'}</p>
        ${nets.tiktok_configured ? `<div class="row wrap"><button class="btn small" id="ttConn">${nets.tiktok && nets.tiktok.connected ? 'Volver a conectar' : 'Conectar TikTok'}</button>${nets.tiktok && nets.tiktok.connected ? '<button class="link" id="ttOff">Desconectar</button>' : ''}</div>` : ''}
        ${nets.tiktok_callback ? `<p class="muted small">Dirección de retorno para la app de TikTok: <code>${esc(nets.tiktok_callback)}</code><br>Prefijo de URL que hay que verificar en TikTok: <code>${esc(nets.tiktok_url_prefix)}</code><br>Condiciones de uso: <code>${esc(nets.terms_url)}</code><br>Política de privacidad: <code>${esc(nets.privacy_url)}</code></p>` : ''}</div>
      ${nets.public_url_ok === false ? '<p class="error small">Falta PUBLIC_BASE_URL (https://…) en Railway: las redes no podrían descargar la imagen.</p>' : ''}</section>
    <section class="panel"><h2>Web</h2><p>${h.auto_publish ? '«Aprobar y publicar» envía la noticia a infolinense.com.' : 'La publicación en infolinense.com no está configurada: «Aprobar y publicar» solo aprueba.'}</p></section>
    <section class="panel"><h2>Fuentes</h2>
      <div class="sources">${srcs.map(s => `<div class="srcrow"><label class="switch"><input type="checkbox" ${s.active ? 'checked' : ''} onchange="toggleSource(${s.id})"> ${esc(s.name)}</label>${s.last_error ? `<span class="error small">Error</span>` : `<span class="muted small">${s.items_added ?? 0} nuevas</span>`}</div>`).join('')}</div>
      <form id="addSrc" class="stack"><input name="name" placeholder="Nombre del medio o página" required><input name="url" type="url" placeholder="https://… (RSS, portada, Facebook o Instagram)" required>
      <select name="kind"><option value="rss">RSS</option><option value="html">Portada web</option><option value="social">Facebook / Instagram</option></select><button class="btn small">Añadir fuente</button></form></section>
    <section class="panel"><h2>Páginas y grupos de Facebook vigilados</h2><p class="muted small">Solo lo público. Se buscan quejas, propuestas y asociaciones.</p>
      <div class="sources" id="fbList"></div>
      <form id="addFb" class="stack"><input name="name" placeholder="Nombre (p. ej. AVV La Atunara)" required><input name="url" type="url" placeholder="https://www.facebook.com/groups/…" required><button class="btn small">Vigilar</button></form></section>
    <button class="btn" onclick="logout()">Salir</button>`;
  api('/api/social').then(d => { $('#fbList').innerHTML = d.watched.map(s => `<div class="srcrow"><label class="switch"><input type="checkbox" ${s.active ? 'checked' : ''} onchange="toggleSource(${s.id})"> ${esc(s.name)}</label></div>`).join('') || '<p class="muted small">Ninguna todavía.</p>' }).catch(() => { });
  $('#addFb').onsubmit = async e => { e.preventDefault(); const d = Object.fromEntries(new FormData(e.target)); try { await api('/api/sources', { method: 'POST', body: JSON.stringify({ ...d, kind: 'social', priority: 60, local_scope: true }) }); toast('Ahora se vigila'); render() } catch (er) { toast(er.message) } };
  $('#aiCheck').onclick = async e => {
    e.target.disabled = true; e.target.textContent = 'Comprobando…';
    try { const r = await api('/api/ai/check'); $('#aiOut').innerHTML = r.providers.filter(p => p.configured).map(p => `<p class="${p.ok ? 'ok' : 'error'}"><b>${esc(p.provider_name)}</b> ${p.active ? '(en uso)' : ''}: ${p.ok === null ? esc(p.message) : p.ok ? 'funciona' : esc(p.message) + (p.http_status ? ` <small>(HTTP ${p.http_status} ${esc(p.code || '')})</small>` : '')}</p>`).join('') || '<p class="error">Sin claves configuradas.</p>' }
    catch (er) { $('#aiOut').innerHTML = `<p class="error">${esc(er.message)}</p>` } finally { e.target.disabled = false; e.target.textContent = 'Comprobar ahora' }
  };
  $('#photoCheck').onclick = async e => {
    e.target.disabled = true; e.target.textContent = 'Comprobando…';
    try { const r = await api('/api/photos/check'); $('#photoOut').innerHTML = r.providers.map(p => `<p class="${p.count ? 'ok' : p.count === null ? 'muted' : 'error'}"><b>${esc(p.name)}</b>: ${p.count === null ? 'sin configurar' : p.count + ' fotos'}</p>`).join('') }
    catch (er) { $('#photoOut').innerHTML = `<p class="error">${esc(er.message)}</p>` } finally { e.target.disabled = false; e.target.textContent = 'Comprobar fotos' }
  };
  const conn = async net => { try { location.href = (await api(`/api/networks/${net}/connect`)).url } catch (e) { toast(e.message) } };
  const off = async net => { if (!confirm('¿Desconectar?')) return; try { await api(`/api/networks/${net}/disconnect`, { method: 'POST' }); render() } catch (e) { toast(e.message) } };
  if ($('#metaConn')) $('#metaConn').onclick = () => conn('meta');
  if ($('#metaTok')) $('#metaTok').onsubmit = async e => { e.preventDefault(); const b = e.target.querySelector('button'); b.disabled = true; b.textContent = 'Conectando…'; try { const r = await api('/api/networks/meta/token', { method: 'POST', body: JSON.stringify({ token: e.target.token.value }) }); toast('Conectado: ' + r.name + (r.instagram ? ' · @' + r.instagram : '')); render() } catch (er) { toast(er.message); b.disabled = false; b.textContent = 'Conectar con este token' } };
  if ($('#ttConn')) $('#ttConn').onclick = () => conn('tiktok');
  if ($('#igConn')) $('#igConn').onclick = () => conn('instagram');
  if ($('#igOff')) $('#igOff').onclick = () => off('instagram');
  if ($('#metaOff')) $('#metaOff').onclick = () => off('meta');
  if ($('#ttOff')) $('#ttOff').onclick = () => off('tiktok');
  if ($('#pagePick')) $('#pagePick').onchange = async e => { try { const r = await api('/api/networks/meta/page', { method: 'POST', body: JSON.stringify({ page_id: e.target.value }) }); toast('Página: ' + r.name); render() } catch (er) { toast(er.message) } };
  const cc = $('#canvaConn'); if (cc) cc.onclick = async () => { try { location.href = (await api('/api/canva/connect')).url } catch (e) { toast(e.message) } };
  $('#addSrc').onsubmit = async e => { e.preventDefault(); const d = Object.fromEntries(new FormData(e.target)); try { await api('/api/sources', { method: 'POST', body: JSON.stringify({ ...d, priority: 60 }) }); toast('Fuente añadida'); render() } catch (er) { toast(er.message) } };
}
window.toggleSource = async id => { try { await api(`/api/sources/${id}/toggle`, { method: 'POST' }) } catch (e) { toast(e.message) } };
window.go = go; window.download = download; window.logout = logout; window.render = render;

if (token) start(); else logout();
