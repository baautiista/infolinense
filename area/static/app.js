/* Área Campo de Gibraltar Desk — 1 Ordenar · 2 Redacción · 3 Revisar · 4 Copiar */
const $ = s => document.querySelector(s);
let token = localStorage.getItem('area_token') || '';
let view = 'sort', editorId = null, editorFrom = 'review', block = null, showArea = false, sortMode = 'tenders';
const TF = { town: '', status: '', sort: 'new', q: '', sel: new Set(), area: 0 };
let blobUrls = [];
let queue = [];
const BLOCKS = ['Exclusivas', 'Obras y urbanismo', 'Agenda y cultura', 'Instituciones', 'Nacionales adaptables', 'Efemérides'];
const TAG = { licitacion: 'Licitación', edicto: 'Edicto', presupuesto: 'Presupuesto', obras: 'Obras', asi_sera: 'Así será', agenda: 'Agenda', efemeride: 'Efeméride' };
const PRIO = [['urgent', 'Urgente'], ['today', 'Hoy'], ['week', 'Esta semana'], ['later', 'Más adelante']];
const KIND = { carrusel: 'Carrusel generado', canva: 'Carrusel Canva', doc: 'Del expediente', plano: 'Planos', subida: 'Subidas', fuente: 'De la fuente', medio: 'Otros medios', internet: 'Internet' };
const TSTATUS = { PUB: 'En plazo', PRE: 'Anuncio previo', EV: 'En evaluación', ADJ: 'Adjudicada', RES: 'Formalizada', MENOR: 'Contrato menor', ANUL: 'Anulada', RESUMEN: 'Resumen' };

/* ---------- utilidades ---------- */
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m])) }
function safeUrl(s) { try { const u = new URL(s); return ['http:', 'https:'].includes(u.protocol) ? esc(u.href) : '#' } catch { return '#' } }
function host(u) { try { return new URL(u).hostname.replace('www.', '') } catch { return '' } }
function toast(t) { const el = $('#toast'); el.textContent = t; el.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => el.hidden = true, 3500) }
const wait = ms => new Promise(r => setTimeout(r, ms));
function ago(iso) {
  if (!iso) return '';
  const d = new Date(iso), now = new Date(), min = Math.round((now - d) / 60000);
  if (/T09:00:00(\.000)?(\+00:00|Z)$/.test(iso)) return d.toLocaleDateString('es-ES', { timeZone: 'Europe/Madrid', day: 'numeric', month: 'short' });
  if (min < 60) return `hace ${Math.max(1, min)} min`;
  if (min < 600) return `hace ${Math.round(min / 60)} h`;
  return d.toLocaleDateString('es-ES', { timeZone: 'Europe/Madrid', day: 'numeric', month: 'short' }) + ' ' + d.toLocaleTimeString('es-ES', { timeZone: 'Europe/Madrid', hour: '2-digit', minute: '2-digit' });
}
async function request(url, opt = {}) {
  const headers = { ...(opt.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }), ...(token ? { Authorization: `Bearer ${token}` } : {}) };
  const r = await fetch(url, { ...opt, headers });
  if (r.status === 401 && url !== '/api/auth/login') { logout(); throw new Error('La sesión ha caducado') }
  if (!r.ok) { let d = await r.text(); try { d = JSON.parse(d).detail || d } catch { } throw new Error(typeof d === 'string' ? d : JSON.stringify(d)) }
  return r;
}
async function api(url, opt) { const r = await request(url, opt); return r.headers.get('content-type')?.includes('json') ? r.json() : r.text() }
const post = (url, body) => api(url, { method: 'POST', body: JSON.stringify(body || {}) });
async function blobUrl(url) { const u = URL.createObjectURL(await (await request(url)).blob()); blobUrls.push(u); return u }
async function download(url, name) { try { toast('Preparando descarga…'); const a = document.createElement('a'); a.href = await blobUrl(url); a.download = name; document.body.appendChild(a); a.click(); a.remove() } catch (e) { toast(e.message) } }
async function copy(text, btn) {
  try { await navigator.clipboard.writeText(text) } catch { const t = document.createElement('textarea'); t.value = text; document.body.appendChild(t); t.select(); document.execCommand('copy'); t.remove() }
  if (btn) { const old = btn.textContent; btn.textContent = 'Copiado ✓'; btn.classList.add('done'); setTimeout(() => { btn.textContent = old; btn.classList.remove('done') }, 1600) }
}

/* ---------- sesión y navegación ---------- */
function logout() { token = ''; localStorage.removeItem('area_token'); $('#app').hidden = true; $('#login').hidden = false }
$('#loginForm').onsubmit = async e => {
  e.preventDefault(); $('#loginError').textContent = '';
  try { token = (await post('/api/auth/login', { password: $('#password').value })).token; localStorage.setItem('area_token', token); $('#password').value = ''; start() }
  catch (err) { $('#loginError').textContent = err.message }
};
function start() { $('#login').hidden = true; $('#app').hidden = false; go(location.hash.slice(1) || 'sort') }
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => go(b.dataset.view));
$('#settingsBtn').onclick = () => go('settings');
$('#backBtn').onclick = () => go(editorFrom);
window.onhashchange = () => { const h = location.hash.slice(1); if (h && h !== cur()) go(h, true) };
function cur() { return view === 'editor' ? `editor/${editorId}` : view }
function go(target, fromHash) {
  const [v, id] = target.split(/[/?]/);
  if (v === 'editor' && view !== 'editor') editorFrom = ['sort', 'writing', 'review', 'ready'].includes(view) ? view : 'review';
  view = ['sort', 'writing', 'review', 'ready', 'settings', 'editor'].includes(v) ? v : 'sort';
  editorId = view === 'editor' ? Number(id) : null;
  document.body.classList.toggle('wide', view === 'editor');
  if (!fromHash) location.hash = cur();
  document.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('on', b.dataset.view === (view === 'editor' ? editorFrom : view)));
  $('#title').textContent = { sort: 'Ordenar', writing: 'Redacción', review: 'Revisar', ready: 'Copiar', settings: 'Ajustes', editor: 'Pieza' }[view];
  $('#backBtn').hidden = view !== 'editor';
  render(); counts();
}
async function counts() {
  try {
    const w = await api('/api/workflow');
    const n = { sort: w.sort, writing: w.writing, review: w.review, ready: w.ready };
    document.querySelectorAll('.tabs button').forEach(b => { b.querySelector('.n').textContent = n[b.dataset.view] || '' });
  } catch { }
}
async function render() {
  blobUrls.forEach(URL.revokeObjectURL); blobUrls = [];
  const v = $('#view'); v.innerHTML = '<p class="empty"><span class="spin"></span> Cargando…</p>'; window.scrollTo(0, 0);
  try { await ({ sort: sortView, writing: writingView, review: reviewView, ready: readyView, settings: settingsView, editor: editorView }[view])(v) }
  catch (e) { if (token) v.innerHTML = `<p class="empty">${esc(e.message)}</p>` }
}

/* ---------- Buscar ---------- */
$('#scanBtn').onclick = async () => {
  const b = $('#scanBtn'); b.disabled = true; b.textContent = 'Buscando…';
  try {
    await post('/api/scan');
    for (let i = 0; i < 200; i++) { await wait(4000); const s = await api('/api/scan'); if (!s.running) { toast(`${s.added} nuevas${s.errors.length ? ` · ${s.errors.length} fuentes con error (ver Ajustes)` : ''}`); break } }
    if (view === 'sort') render(); counts();
  } catch (e) { toast(e.message) } finally { b.disabled = false; b.textContent = 'Buscar' }
};

/* ---------- etiquetas de una propuesta ---------- */
function badges(c) {
  const out = [];
  if (c.exclusive) out.push('<span class="tag excl">EXCLUSIVA</span>');
  if (c.area_state === 'update') out.push(`<span class="tag upd">Actualización de Área</span>`);
  if (c.area_state === 'published') out.push(`<span class="tag area">Ya en Área</span>`);
  if (c.tag) out.push(`<span class="tag">${esc(TAG[c.tag] || c.tag)}</span>`);
  (c.towns || '').split(',').filter(Boolean).forEach(t => out.push(`<span class="tag town">${esc(t)}</span>`));
  (c.competitors || []).slice(0, 3).forEach(x => out.push(`<span class="tag comp" title="${esc(x.title)}">Ya lo tiene ${esc(x.outlet)}</span>`));
  return out.join(' ');
}
function facts(c) {
  const f = [['Organismo', c.organism], ['Importe', c.amount], ['Plazo', c.deadline], ['Documentos', c.docs && c.docs.length ? `${c.docs.length} en el expediente` : '']].filter(x => x[1]);
  return f.length ? `<div class="facts">${f.map(x => `<div class="fact"><b>${x[0]}</b>${esc(x[1])}</div>`).join('')}</div>` : '';
}

/* ---------- Fase 1: Ordenar ---------- */
async function sortView(v) {
  const [today, w] = await Promise.all([api('/api/today'), api('/api/workflow')]);
  v.innerHTML = `${pickCard(today)}
    <div class="chips" style="margin:16px 0 4px"><button class="chip ${sortMode === 'tenders' ? 'on' : ''}" data-mode="tenders">Licitaciones de la comarca</button>
      <button class="chip ${sortMode === 'rest' ? 'on' : ''}" data-mode="rest">Resto del radar</button></div>
    <div id="sortBody"></div>`;
  v.querySelectorAll('[data-mode]').forEach(b => b.onclick = () => { sortMode = b.dataset.mode; sortView(v) });
  bindPick(today);
  if (sortMode === 'tenders') return tendersView($('#sortBody'));
  const q = await api('/api/sort-queue' + (showArea ? '?area=1' : ''));
  queue = q.filter(x => showArea || x.group !== 'Licitaciones');
  const groups = showArea ? ['Ya en Área'] : BLOCKS;
  if (showArea) queue.forEach(x => x.group = 'Ya en Área');
  const count = g => queue.filter(n => n.group === g).length;
  if (!block || !groups.includes(block) || !count(block)) block = groups.find(count) || groups[0];
  $('#sortBody').innerHTML = `${efeStrip(today.efemerides)}
    <div class="chips scroll" style="margin-top:14px">${groups.map(g => `<button class="chip ${g === block ? 'on' : ''}" data-g="${esc(g)}" ${count(g) ? '' : 'disabled'}>${esc(g)}<b>${count(g)}</b></button>`).join('')}
      <button class="chip ${showArea ? 'on' : ''}" id="areaToggle">${showArea ? '← Volver al radar' : `Ya en Área<b>${w.in_area}</b>`}</button></div>
    <div id="focus"></div>`;
  v.querySelectorAll('[data-g]').forEach(b => b.onclick = () => { block = b.dataset.g; sortView(v) });
  $('#areaToggle').onclick = () => { showArea = !showArea; block = null; sortView(v) };
  drawFocus();
}

/* ---------- Licitaciones: todas las de la comarca, por municipio y fase ---------- */
const euro = n => n ? Number(n).toLocaleString('es-ES', { maximumFractionDigits: 0 }) + ' €' : '';
async function tendersView(box) {
  const qs = new URLSearchParams({ town: TF.town, status: TF.status, sort: TF.sort, q: TF.q, area: TF.area });
  const d = await api('/api/tenders?' + qs);
  const all = d.towns.reduce((a, t) => a + t.n, 0);
  box.innerHTML = `<div class="chips scroll" style="margin-top:12px"><button class="chip ${!TF.town ? 'on' : ''}" data-town="">Todos<b>${all}</b></button>
      ${d.towns.map(t => `<button class="chip ${TF.town === t.town ? 'on' : ''}" data-town="${esc(t.town)}" ${t.n ? '' : 'disabled'}>${esc(t.town)}<b>${t.n}</b></button>`).join('')}</div>
    <div class="chips scroll"><button class="chip ${!TF.status ? 'on' : ''}" data-st="">Todas las fases</button>
      ${d.statuses.filter(x => x.n || TF.status === x.code).map(x => `<button class="chip ${TF.status === x.code ? 'on' : ''}" data-st="${x.code}">${esc(x.label)}<b>${x.n}</b></button>`).join('')}
      <button class="chip ${TF.area ? 'on' : ''}" id="tarea">${TF.area ? '← Volver a las no publicadas' : `Ya publicadas en Área<b>${d.in_area}</b>`}</button></div>
    <div class="row wrap" style="margin-bottom:10px"><input id="tq" class="grow" placeholder="Buscar (obra, calle, empresa…)" value="${esc(TF.q)}" style="min-width:180px">
      <select id="tsort" style="width:auto">${[['new', 'Más recientes'], ['amount', 'Mayor importe'], ['deadline', 'Plazo más cercano'], ['score', 'Más interesantes']].map(([k, l]) => `<option value="${k}" ${TF.sort === k ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
    <p class="progress">${TF.area ? 'Ya publicadas en Área (no se proponen): ' : ''}${d.total} licitaciones${d.sum ? ' · ' + euro(d.sum) + ' en total' : ''}</p>
    <div class="list">${d.items.map(tenderRow).join('') || '<p class="empty">No hay licitaciones con estos filtros. Pulsa Buscar para leer la Plataforma de Contratación.</p>'}</div>
    <div class="actions" id="tsel" ${TF.sel.size >= 2 ? '' : 'hidden'}><button class="btn primary" id="tsum">Pieza resumen con ${TF.sel.size} licitaciones</button><button class="btn" id="tclear">Quitar selección</button></div>`;
  box.querySelectorAll('[data-town]').forEach(b => b.onclick = () => { TF.town = b.dataset.town; tendersView(box) });
  box.querySelectorAll('[data-st]').forEach(b => b.onclick = () => { TF.status = b.dataset.st; tendersView(box) });
  $('#tsort').onchange = e => { TF.sort = e.target.value; tendersView(box) };
  $('#tarea').onclick = () => { TF.area = TF.area ? 0 : 1; tendersView(box) };
  $('#tq').onkeydown = e => { if (e.key === 'Enter') { TF.q = e.target.value; tendersView(box) } };
  box.querySelectorAll('[data-sel]').forEach(c => c.onchange = () => { c.checked ? TF.sel.add(+c.dataset.sel) : TF.sel.delete(+c.dataset.sel); $('#tsel').hidden = TF.sel.size < 2; $('#tsum').textContent = `Pieza resumen con ${TF.sel.size} licitaciones` });
  $('#tclear').onclick = () => { TF.sel.clear(); tendersView(box) };
  $('#tsum').onclick = async () => { try { await post('/api/tenders/summary', { ids: [...TF.sel] }); TF.sel.clear(); toast('Redactando la pieza resumen'); go('writing') } catch (e) { toast(e.message) } };
  box.querySelectorAll('[data-write]').forEach(b => b.onclick = async () => { b.disabled = true; b.innerHTML = '<span class="spin"></span>'; await post(`/api/candidates/${b.dataset.write}/triage`, { priority: 'today' }); toast('A redacción'); b.textContent = 'En redacción'; counts() });
  box.querySelectorAll('[data-open]').forEach(b => b.onclick = () => go('editor/' + b.dataset.open));
  box.querySelectorAll('[data-no]').forEach(b => b.onclick = async () => { await post(`/api/candidates/${b.dataset.no}/triage`, { priority: 'no' }); b.closest('.card').remove(); counts() });
}
function tenderRow(c) {
  const name = c.title.replace(/^[^:]{3,30}:\s*/, '');
  const busy = ['queued', 'working'].includes(c.work_state);
  return `<div class="card" style="padding:12px 14px"><div class="meta"><label class="row" style="gap:6px;font-weight:700"><input type="checkbox" data-sel="${c.id}" ${TF.sel.has(c.id) ? 'checked' : ''} style="width:18px;height:18px"></label>
      <span class="tag ${c.tstatus === 'PUB' ? 'excl' : c.tstatus === 'ADJ' || c.tstatus === 'RES' ? 'area' : ''}">${esc(TSTATUS[c.tstatus] || c.tstatus)}</span><span class="tag town">${esc(c.town)}</span>
      ${c.exclusive ? '<span class="tag excl">EXCLUSIVA</span>' : ''}${c.area_state === 'published' ? '<span class="tag area">Ya en Área</span>' : c.area_state === 'update' ? '<span class="tag upd">Actualiza a Área</span>' : ''}
      ${(c.competitors || []).slice(0, 2).map(x => `<span class="tag comp">${esc(x.outlet)}</span>`).join('')}<span>${ago(c.published_at)}</span></div>
    <h3 style="margin:8px 0 4px;font:800 17px/1.3 Poppins,sans-serif">${esc(name)}</h3>
    <div class="meta"><span class="src">${esc(c.organism || c.source_name || '')}</span>${c.amount_value ? `<b style="color:var(--ink);font-size:15px">${euro(c.amount_value)}</b>` : ''}
      ${c.deadline && c.tstatus === 'PUB' ? `<span>Plazo: ${esc(c.deadline)}</span>` : ''}${c.winner ? `<span>Adjudicataria: <b>${esc(c.winner)}</b></span>` : ''}${c.docs && c.docs.length ? `<span>${c.docs.length} documentos</span>` : ''}${(c.links || []).length ? `<span>+${c.links.length} fuentes</span>` : ''}</div>
    <div class="row wrap" style="margin-top:10px">${c.article_id ? `<button class="btn small primary" data-open="${c.article_id}">Abrir pieza</button>` : busy ? '<span class="small muted"><span class="spin"></span> Redactando…</span>' : `<button class="btn small primary" data-write="${c.id}">Redactar</button>`}
      <a class="btn small" href="${safeUrl(c.url)}" target="_blank" rel="noopener">Expediente ↗</a><button class="btn small" data-no="${c.id}">No interesa</button></div></div>`;
}
function pickCard(t) {
  const c = t.pick || t.suggestion;
  if (!c) return `<div class="card pick"><div class="label">Pieza del día</div><p class="muted">Todavía no hay propuesta. Pulsa <b>Buscar</b> para leer todas las fuentes.</p></div>`;
  const btn = t.article_id ? `<button class="btn primary" id="pickOpen">Abrir la pieza</button>`
    : t.pick ? `<button class="btn primary" id="pickWrite">${c.work_state === 'queued' || c.work_state === 'working' ? '<span class="spin"></span> Redactando…' : 'Redactar ahora'}</button>`
      : `<button class="btn primary" id="pickChoose">Elegir como pieza de hoy</button>`;
  return `<div class="card pick"><div class="label">${t.pick ? 'Pieza del día' : 'Propuesta para hoy'}</div>
    <h2>${esc(c.title)}</h2><div class="meta"><span class="src">${esc(c.outlet || c.source_name || '')}</span><span>${ago(c.published_at)}</span>${badges(c)}</div>
    ${facts(c)}<div class="row wrap" style="margin-top:10px">${btn}<a class="btn small" href="${safeUrl(c.url)}" target="_blank" rel="noopener">Ver fuente</a>
    <button class="link" id="pickOther">Proponer otra</button></div></div>`;
}
function bindPick(t) {
  const c = t.pick || t.suggestion;
  $('#pickOpen') && ($('#pickOpen').onclick = () => go('editor/' + t.article_id));
  $('#pickWrite') && ($('#pickWrite').onclick = async () => { await post(`/api/candidates/${c.id}/write`); toast('Redactando la pieza del día'); go('writing') });
  $('#pickChoose') && ($('#pickChoose').onclick = async () => { await post(`/api/candidates/${c.id}/triage`, { priority: 'today' }); toast('Elegida y en redacción'); go('writing') });
  $('#pickOther') && ($('#pickOther').onclick = async () => { try { await post('/api/today'); render() } catch (e) { toast(e.message) } });
}
function efeStrip(list) {
  if (!list || !list.length) return '';
  return `<div class="sect">Efemérides próximas</div><div class="efes">${list.map(e => `<div class="efe ${e.round ? 'round' : ''}"><b>${e.in_days === 0 ? 'HOY' : e.in_days === 1 ? 'Mañana' : 'En ' + e.in_days + ' días'} · ${new Date(e.date).toLocaleDateString('es-ES', { day: 'numeric', month: 'short' })}${e.years ? ' · ' + e.years + ' años' : ''}</b>${esc(e.title)}${e.verified ? '' : ' <span class="tag upd">verificar</span>'}</div>`).join('')}</div>`;
}
function drawFocus() {
  const box = $('#focus'); if (!box) return;
  const items = queue.filter(n => n.group === block);
  const c = items[0];
  if (!c) { box.innerHTML = `<p class="empty">Nada pendiente en este bloque.</p>`; return }
  const links = (c.links || []).map(l => `<div class="peeki"><span><a href="${safeUrl(l.url)}" target="_blank" rel="noopener">${esc(l.title)}</a></span><small class="muted">${esc(l.outlet || l.source_name || host(l.url))}</small></div>`).join('');
  const comp = (c.competitors || []).map(x => `<div class="peeki"><span><a href="${safeUrl(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a></span><small class="muted">${esc(x.outlet)}</small></div>`).join('');
  box.innerHTML = `<p class="progress">${items.length} en ${esc(block)}</p>
    <article class="card focuscard" id="fc">
      <div class="meta"><span class="src">${esc(c.outlet || c.source_name || host(c.url))}</span><span>${ago(c.published_at)}</span><span>${c.score} pts</span></div>
      <h2>${esc(c.title)}</h2>
      <div class="meta">${badges(c)}</div>
      ${c.area_state ? `<p class="small" style="margin:8px 0 0">En Área: <a href="${safeUrl(c.area_url)}" target="_blank" rel="noopener">${esc(c.area_title)}</a></p>` : ''}
      ${facts(c)}
      ${c.excerpt ? `<p class="muted">${esc(c.excerpt.slice(0, 420))}${c.excerpt.length > 420 ? '…' : ''}</p>` : ''}
      <a class="link" href="${safeUrl(c.url)}" target="_blank" rel="noopener">Abrir la fuente ↗</a>
      ${links ? `<details class="peek"><summary>Otras fuentes (${c.links.length})</summary>${links}</details>` : ''}
      ${comp ? `<details class="peek"><summary>Quién lo ha publicado ya (${c.competitors.length})</summary>${comp}</details>` : ''}
      <div class="decide">${PRIO.map(([k, l]) => `<button class="btn ${k === 'urgent' ? 'urgent' : ''}" data-p="${k}">${l}</button>`).join('')}<button class="btn no" data-p="no">No interesa</button></div>
    </article>
    ${items.length > 1 ? `<details class="peek"><summary>Siguientes</summary>${items.slice(1, 15).map(n => `<div class="peeki"><span>${n.exclusive ? '<span class="tag excl">EXCL</span> ' : ''}${esc(n.title)}</span><small class="muted">${n.score}</small></div>`).join('')}</details>` : ''}`;
  box.querySelectorAll('[data-p]').forEach(b => b.onclick = async () => {
    b.disabled = true;
    try {
      const r = await post(`/api/candidates/${c.id}/triage`, { priority: b.dataset.p });
      $('#fc').classList.add('gone'); await wait(200);
      queue = queue.filter(x => x.id !== c.id); drawFocus(); counts();
      if (r.queued) toast('A redacción');
    } catch (e) { toast(e.message); b.disabled = false }
  });
}

/* ---------- Fase 2: Redacción ---------- */
async function writingView(v) {
  const rows = await api('/api/drafting');
  if (!rows.length) { v.innerHTML = `<p class="empty">No hay nada redactándose. Marca propuestas como Urgente u Hoy en Ordenar.</p>`; return }
  v.innerHTML = `<div class="list">${rows.map(c => `<div class="card"><div class="meta">${badges(c)}</div><h3 style="margin-top:6px">${esc(c.title)}</h3>
    <p class="${c.work_state === 'error' ? 'error' : 'muted'} small">${c.work_state === 'error' ? esc(c.work_error) : c.work_state === 'done' ? 'Lista para revisar' : `<span class="spin"></span> ${esc(c.work_error || 'En cola')}`}</p>
    <div class="row">${c.article_id ? `<button class="btn small primary" data-open="${c.article_id}">Revisar</button>` : ''}${c.work_state === 'error' ? `<button class="btn small" data-retry="${c.id}">Reintentar</button>` : ''}</div></div>`).join('')}</div>`;
  v.querySelectorAll('[data-open]').forEach(b => b.onclick = () => go('editor/' + b.dataset.open));
  v.querySelectorAll('[data-retry]').forEach(b => b.onclick = async () => { await post(`/api/candidates/${b.dataset.retry}/write`); render() });
  if (rows.some(r => ['queued', 'working'].includes(r.work_state))) { clearTimeout(writingView.t); writingView.t = setTimeout(() => view === 'writing' && render(), 6000) }
}

/* ---------- Fases 3 y 4: listas ---------- */
async function articleList(v, rows, empty) {
  if (!rows.length) { v.innerHTML = `<p class="empty">${empty}</p>`; return }
  v.innerHTML = `<div class="list">${rows.map(a => `<button class="item" data-open="${a.id}"><div class="thumb" data-thumb="${a.cover ? a.cover.id : ''}" ${a.cover && !a.cover.local_path && a.cover.url ? `style="background-image:url('${safeUrl(a.cover.url)}')"` : ''}></div>
    <div class="grow"><div class="meta">${a.exclusive ? '<span class="tag excl">EXCLUSIVA</span>' : ''}${a.area_state === 'update' ? '<span class="tag upd">Actualización</span>' : ''}<span class="tag">${esc(a.section || '')}</span><span class="tag town">${esc(a.town || '')}</span>${a.status === 'published' ? '<span class="ok small">Publicada</span>' : ''}</div>
    <h3>${esc(a.headline)}</h3><p class="muted small" style="margin:0">${esc((a.entradilla || '').slice(0, 160))}</p><p class="muted small" style="margin:4px 0 0">${(a.slides || []).length} diapositivas · ${ago(a.updated_at.replace(' ', 'T') + 'Z')}</p></div></button>`).join('')}</div>`;
  v.querySelectorAll('[data-open]').forEach(b => b.onclick = () => go('editor/' + b.dataset.open));
  for (const el of v.querySelectorAll('[data-thumb]')) { const id = el.dataset.thumb; if (id && !el.style.backgroundImage) { try { el.style.backgroundImage = `url('${await blobUrl('/api/media/' + id + '/file')}')` } catch { } } }
}
async function reviewView(v) { await articleList(v, await api('/api/review'), 'No hay piezas por revisar.') }
async function readyView(v) { await articleList(v, await api('/api/ready'), 'Cuando marques una pieza como «Lista», aparecerá aquí para copiar y descargar.') }

/* ---------- Editor ---------- */
let A = null;
function field(key, label, value, rows, max) {
  return `<div class="field"><div class="head"><b>${label}</b><span class="count" data-count="${key}" data-max="${max || ''}"></span><button class="btn tiny copybtn" data-copy="${key}">Copiar</button></div>
    <textarea data-f="${key}" rows="${rows}">${esc(value || '')}</textarea></div>`;
}
async function editorView(v) {
  A = await api('/api/articles/' + editorId);
  const c = A.candidate, r = A.render || {}, src = (A.sources && A.sources.links) || [], missing = (A.sources && A.sources.missing) || [];
  v.innerHTML = `<div class="editor">
    <div class="card full"><div class="meta">${badges(c)}<span class="tag">${esc(A.section || '')}</span>${A.provider ? `<span class="small muted">${A.provider === 'source' ? 'Borrador sin IA' : 'Redactado con ' + esc(A.provider)}</span>` : ''}</div>
      <p class="small" style="margin:8px 0 0">Fuente: ${src.map(s => `<a href="${safeUrl(s.url)}" target="_blank" rel="noopener">${esc(s.name || host(s.url))}</a>`).join(' · ')}</p>
      ${A.area_note ? `<p class="small"><b>${esc(A.area_note)}</b> <a href="${safeUrl(c.area_url)}" target="_blank" rel="noopener">ver en Área</a></p>` : ''}
      ${missing.length ? `<details class="peek"><summary>Comprobar antes de publicar (${missing.length})</summary><ul>${missing.map(m => `<li>${esc(m)}</li>`).join('')}</ul></details>` : ''}
    </div>
    <section class="card stack">
      <h3>Textos</h3>
      ${field('headline', 'Titular', A.headline, 2, 90)}
      ${(A.headline_options || []).length ? `<div class="alt">${A.headline_options.map(h => `<button data-alt="${esc(h)}">${esc(h)}</button>`).join('')}</div>` : ''}
      <div class="row"><button class="link" data-regen="headlines">Otros titulares</button></div>
      ${field('entradilla', 'Entradilla', A.entradilla, 3, 200)}
      <p class="small muted" style="margin:-6px 0 0">La entradilla da los datos que no están en el titular (dónde, quién, cuánto, plazo). Nunca lo repite.</p>
      ${field('body', 'Texto', A.body, 12)}
      ${field('instagram_copy', 'Copy de Instagram', A.instagram_copy, 9, 2200)}
      <div class="row"><button class="link" data-regen="instagram">Rehacer copy</button></div>
      <div class="row wrap"><button class="btn small" id="copyAll">Copiar todo</button><button class="btn small" id="saveTexts">Guardar cambios</button></div>
    </section>
    <section class="card stack">
      <div class="card render ${r.recommended ? '' : 'no'}"><h3>${r.recommended ? '🏗 Conviene un render: «así quedaría»' : 'Render'}</h3>
        ${r.why ? `<p class="small">${esc(r.why)}</p>` : '<p class="small muted">No hace falta para esta pieza.</p>'}
        ${r.prompt ? `<div class="field"><div class="head"><b>Encargo del render</b><button class="btn tiny copybtn" id="copyRender">Copiar</button></div><p class="small" style="white-space:pre-wrap;margin:0">${esc(r.prompt)}</p>${r.basis ? `<p class="small muted">Basado en: ${esc(r.basis)}</p>` : ''}</div>` : ''}
        <button class="link" data-regen="render">${r.prompt ? 'Rehacer propuesta' : 'Proponer render'}</button></div>
      ${c.docs && c.docs.length ? `<div><h3>Documentos del expediente</h3>${c.docs.map(d => `<div class="peeki"><span><a href="${safeUrl(d.url)}" target="_blank" rel="noopener">${esc(d.name)}</a></span><small class="muted">${esc(d.kind || '')}</small></div>`).join('')}</div>` : ''}
    </section>
    <section class="card stack full">
      <div class="row wrap"><h3 class="grow">Carrusel (${(A.slides || []).length} diapositivas)</h3>
        <select id="addLayout" style="width:auto"><option value="">+ Añadir diapositiva…</option>${Object.entries(CS.LAYOUTS).filter(([k]) => k !== 'portada').map(([k, l]) => `<option value="${k}">${esc(l.label)}</option>`).join('')}</select>
        <button class="btn small" id="saveSlides">Guardar</button><button class="btn tiny" id="copySlides">Copiar textos</button></div>
      <p class="small muted">La portada va siempre primero. Resalta palabras en rojo con ==así== (en azul con ++así++). La foto de cada diapositiva es la imagen que asignes a su número en «Imágenes» (en el mosaico, una por tarjeta, en orden).</p>
      <div id="slides" class="slides"></div>
      <div class="row wrap"><input id="regenSlidesHint" class="grow" placeholder="Indicación para rehacerlo con IA (opcional)"><button class="btn small" data-regen="slides">Rehacer con IA</button>
        <button class="btn small primary" id="genPngs">Generar imágenes del carrusel</button></div>
    </section>
    <section class="card stack full">
      <div class="row wrap"><h3 class="grow">Imágenes</h3>
        <button class="btn small" id="docsBtn">Sacar imágenes del expediente</button>
        <label class="btn small" style="display:inline-flex;align-items:center">Subir<input type="file" id="upload" accept="image/*" multiple hidden></label>
        <button class="btn small primary" id="zipSel">Descargar marcadas (ZIP)</button>
        <button class="btn small" id="zipAll">Descargar todo</button></div>
      <p class="small muted" id="docsState">${docsState(A.docs_status)}</p>
      <div class="row"><input id="imgQ" class="grow" value="${esc(A.photo_query || '')}" placeholder="Buscar fotos en internet"><button class="btn small" id="imgSearch">Buscar</button></div>
      <p class="small muted">Marca con ✓ las que van al carrusel y elige en qué diapositiva va cada una. ⬇ descarga la imagen original.</p>
      <div id="imgs"></div>
    </section>
    <section class="card stack full" id="canvaBox"></section>
    <div class="actions full">
      ${A.status === 'draft' ? '<button class="btn primary" data-st="ready">Marcar lista para copiar</button>' : ''}
      ${A.status === 'ready' ? '<button class="btn primary" data-st="published">Marcar como publicada</button><button class="btn" data-st="draft">Volver a revisión</button>' : ''}
      ${A.status === 'published' ? '<span class="ok">Publicada</span>' : ''}
      <button class="btn" id="rewrite">Volver a redactar</button>
      <button class="btn" data-st="rejected">Descartar</button>
    </div></div>`;
  bindEditor(v); drawImages(); drawCanva(); await loadLogos(); drawSlides();
}
function docsState(s) {
  if (!s) return '';
  if (s.running) return '⏳ Leyendo los documentos del expediente…';
  if (s.error) return 'Expediente: ' + s.error;
  return `Expediente: ${s.documents_read || 0} documentos leídos · ${s.images || 0} imágenes nuevas${(s.errors || []).length ? ' · ' + s.errors.length + ' sin abrir' : ''}${!(s.documents || []).length ? ' · no se encontraron documentos' : ''}`;
}
function headLines(t) {  /* líneas aproximadas en la portada (mayúsculas condensadas, ~24 caracteres por línea) */
  const words = String(t || '').trim().split(/\s+/).filter(Boolean); let lines = words.length ? 1 : 0, cur = 0;
  for (const w of words) { if (cur && cur + 1 + w.length > 24) { lines++; cur = w.length } else cur += (cur ? 1 : 0) + w.length }
  return lines;
}
function counters() {
  document.querySelectorAll('[data-count]').forEach(el => {
    const t = document.querySelector(`[data-f="${el.dataset.count}"]`); const n = t.value.length, max = Number(el.dataset.max);
    if (el.dataset.count === 'headline') {
      const l = headLines(t.value);
      el.textContent = `${n} caracteres · ≈${l} líneas en portada${l >= 5 ? ' (demasiado largo)' : l <= 1 ? ' (muy corto)' : ''}`; el.classList.toggle('over', l >= 5); return;
    }
    el.textContent = max ? `${n}/${max}` : `${n}`; el.classList.toggle('over', max && n > max);
  });
}
function textsNow() { const o = {}; document.querySelectorAll('[data-f]').forEach(t => o[t.dataset.f] = t.value); return o }
function slidesText() {
  const P = CS.plain;
  return (A.slides || []).map((s, i) => [`${i + 1}. ${s.kicker ? P(s.kicker) + ' · ' : ''}${P(s.title)}`, P(s.text), ...(s.bullets || []).map(b => '· ' + P(b)),
    (s.chips || []).length ? s.chips.join(' → ') : '', [s.status ? 'Estado: ' + s.status : '', s.figure, P(s.figure_label)].filter(Boolean).join(' '),
    ...CS.cards(s.cards).map(c => `· ${c.label || ''} ${c.figure || ''} ${P(c.text)}`.trim())].filter(Boolean).join('\n')).join('\n\n')
}
function bindEditor(v) {
  counters();
  let syncT;
  v.querySelectorAll('[data-f]').forEach(t => t.oninput = () => {
    counters();
    if (['headline', 'entradilla'].includes(t.dataset.f) && A.slides && A.slides[0]) {  /* la portada sigue al titular y la entradilla */
      const el = document.querySelector(`.slide2[data-i="0"] [data-s="${t.dataset.f === 'headline' ? 'title' : 'text'}"]`);
      if (el) el.value = t.value;
      clearTimeout(syncT); syncT = setTimeout(() => { readSlides(); preview(0) }, 400);
    }
  });
  v.querySelectorAll('[data-copy]').forEach(b => b.onclick = () => copy(document.querySelector(`[data-f="${b.dataset.copy}"]`).value, b));
  v.querySelectorAll('[data-alt]').forEach(b => b.onclick = () => { const t = document.querySelector('[data-f="headline"]'); const old = t.value; t.value = b.dataset.alt; b.dataset.alt = old; b.textContent = old; counters() });
  $('#copyAll').onclick = e => { const t = textsNow(); copy(`${t.headline}\n\n${t.entradilla}\n\n${t.body}\n\n—\n\n${t.instagram_copy}`, e.target) };
  $('#saveTexts').onclick = async () => { try { A = { ...A, ...(await api('/api/articles/' + A.id, { method: 'PUT', body: JSON.stringify(textsNow()) })) }; toast('Guardado') } catch (e) { toast(e.message) } };
  $('#copySlides').onclick = e => copy(slidesText(), e.target);
  $('#addLayout').onchange = e => { if (!e.target.value) return; readSlides(); A.slides.push({ layout: e.target.value, title: '', text: '', bullets: [], cards: [] }); e.target.value = ''; drawSlides() };
  $('#saveSlides').onclick = saveSlides;
  $('#genPngs').onclick = generatePngs;
  $('#copyRender') && ($('#copyRender').onclick = e => copy(A.render.prompt, e.target));
  v.querySelectorAll('[data-regen]').forEach(b => b.onclick = async () => {
    b.disabled = true; const old = b.textContent; b.innerHTML = '<span class="spin"></span>';
    try { await saveAll(true); await post(`/api/articles/${A.id}/regenerate`, { what: b.dataset.regen, extra: b.dataset.regen === 'slides' ? $('#regenSlidesHint').value : '' }); render() }
    catch (e) { toast(e.message); b.disabled = false; b.textContent = old }
  });
  $('#docsBtn').onclick = async () => { await post(`/api/articles/${A.id}/images/docs`); $('#docsState').textContent = docsState({ running: true }); pollDocs() };
  $('#imgSearch').onclick = async () => { const b = $('#imgSearch'); b.disabled = true; b.innerHTML = '<span class="spin"></span>'; try { const r = await post(`/api/articles/${A.id}/images/search`, { q: $('#imgQ').value }); A.media = r.media; drawImages() } catch (e) { toast(e.message) } b.disabled = false; b.textContent = 'Buscar' };
  $('#upload').onchange = async e => { for (const f of e.target.files) { const fd = new FormData(); fd.append('file', f); try { const r = await api(`/api/articles/${A.id}/media`, { method: 'POST', body: fd }); A.media = r.media } catch (err) { toast(err.message) } } drawImages() };
  $('#zipSel').onclick = () => download(`/api/articles/${A.id}/zip`, 'area-carrusel.zip');
  $('#zipAll').onclick = () => download(`/api/articles/${A.id}/zip?all=1`, 'area-todo.zip');
  $('#rewrite').onclick = async () => { if (!confirm('Se volverá a redactar desde la fuente y se perderán los cambios de texto. ¿Seguro?')) return; await post(`/api/articles/${A.id}/rewrite`); go('writing') };
  v.querySelectorAll('[data-st]').forEach(b => b.onclick = async () => {
    const st = b.dataset.st;
    if (st === 'rejected' && !confirm('¿Descartar esta pieza?')) return;
    if (st !== 'rejected') await saveAll(true);
    const url = st === 'published' ? prompt('Enlace de la publicación (opcional)') : null;
    await post(`/api/articles/${A.id}/status`, { status: st, published_url: url || null });
    toast({ ready: 'Lista: la tienes en Copiar', published: 'Marcada como publicada', draft: 'Vuelve a Revisar', rejected: 'Descartada' }[st]);
    go(st === 'ready' ? 'ready' : st === 'draft' ? 'review' : editorFrom);
  });
  if (A.docs_status && A.docs_status.running) pollDocs();
}
async function pollDocs() {
  const id = A.id;
  for (let i = 0; i < 90; i++) {
    await wait(5000); if (view !== 'editor' || editorId !== id) return;
    const r = await api('/api/articles/' + id); $('#docsState').textContent = docsState(r.docs_status);
    if (!r.docs_status || !r.docs_status.running) { A.media = r.media; drawImages(); return }
  }
}
async function saveAll(silent) { readSlides(); await api('/api/articles/' + A.id, { method: 'PUT', body: JSON.stringify({ ...textsNow(), slides: A.slides }) }); if (!silent) toast('Guardado') }

/* ---------- diapositivas: editor + vista previa con el estilo de Área ---------- */
const mediaUrl = {}, brandLogos = {};
async function mediaBlob(id) { if (!mediaUrl[id]) mediaUrl[id] = blobUrl('/api/media/' + id + '/file').catch(() => ''); return mediaUrl[id] }
async function loadLogos() {
  try { const b = await api('/api/brand'); for (const k of ['blanco', 'color']) brandLogos[k] = b[k] ? await blobUrl('/api/brand/logo/' + k + '?t=' + Date.now()) : '' } catch { }
}
async function slideCtx(i) {
  const base = (A.media || []).filter(m => !['carrusel', 'canva'].includes(m.kind));
  let list = base.filter(m => m.slide === i + 1);
  if (!list.length && i === 0) list = base.filter(m => m.selected).slice(0, 1);
  const images = [];
  for (const m of list) { const u = await mediaBlob(m.id); if (u) images.push(u) }
  return { images, logoWhite: brandLogos.blanco, logoColor: brandLogos.color };
}
function slideFields(s, i) {
  const L = CS.LAYOUTS[s.layout] || CS.LAYOUTS.lista, F = CS.FIELD_LABEL;
  const val = k => k === 'bullets' || k === 'chips' ? (s[k] || []).join('\n') : k === 'cards' ? CS.cards(s.cards).map(c => [c.label, c.figure, c.text, c.icon].map(x => x || '').join(' | ')).join('\n')
    : k === 'streets' ? CS.cards(s.cards).map(c => (c.label || '') + ' | ' + (c.text || '')).join('\n') : (s[k] || '');
  return L.fields.map(k => {
    const multi = ['text', 'bullets', 'chips', 'cards', 'streets'].includes(k) || (k === 'title' && s.layout === 'portada');
    return `<label class="small">${F[k]}${multi ? `<textarea data-s="${k}" rows="${['cards', 'bullets', 'streets'].includes(k) ? 5 : 2}">${esc(val(k))}</textarea>` : `<input data-s="${k}" value="${esc(val(k))}">`}</label>`;
  }).join('') + `<label class="small">Imagen sugerida<input data-s="image_hint" value="${esc(s.image_hint || '')}"></label>`;
}
async function drawSlides() {
  const box = $('#slides');
  box.innerHTML = (A.slides || []).map((s, i) => `<div class="slide2" data-i="${i}">
    <div class="pv"><div class="pvin"></div><button class="btn tiny pvdl" data-dl title="Descargar PNG">⬇ PNG</button></div>
    <div class="stack" style="gap:8px"><div class="row"><span class="n">${i + 1}</span>
      <select data-s="layout" ${i === 0 ? 'disabled' : ''}>${Object.entries(CS.LAYOUTS).filter(([k]) => i === 0 ? k === 'portada' : k !== 'portada').map(([k, l]) => `<option value="${k}" ${s.layout === k ? 'selected' : ''}>${esc(l.label)}</option>`).join('')}</select>
      <span class="grow"></span>${i ? `<button class="btn tiny" data-mv="-1">↑</button><button class="btn tiny" data-mv="1">↓</button><button class="btn tiny" data-del>✕</button>` : ''}</div>
      <p class="small muted" style="margin:0">${esc((CS.LAYOUTS[s.layout] || {}).help || '')}</p>${slideFields(s, i)}</div></div>`).join('');
  box.querySelectorAll('.slide2').forEach(el => {
    const i = +el.dataset.i;
    let t;
    el.querySelectorAll('[data-s]').forEach(x => x.oninput = () => { clearTimeout(t); t = setTimeout(() => { readSlides(); preview(i) }, 350) });
    el.querySelector('[data-s="layout"]').onchange = () => { readSlides(); drawSlides() };
    el.querySelectorAll('[data-mv]').forEach(b => b.onclick = () => { readSlides(); const j = i + +b.dataset.mv; if (j < 1 || j >= A.slides.length) return;[A.slides[i], A.slides[j]] = [A.slides[j], A.slides[i]]; drawSlides() });
    el.querySelector('[data-del]') && (el.querySelector('[data-del]').onclick = () => { readSlides(); A.slides.splice(i, 1); drawSlides() });
    el.querySelector('[data-dl]').onclick = async e => { e.target.disabled = true; try { const png = await renderPng(i); const a = document.createElement('a'); a.href = png; a.download = `area_${A.id}_${String(i + 1).padStart(2, '0')}.png`; a.click() } catch (err) { toast('No se pudo generar: ' + err.message) } e.target.disabled = false };
  });
  await document.fonts.ready;
  for (let i = 0; i < (A.slides || []).length; i++) await preview(i);
}
async function preview(i) {
  const el = document.querySelector(`.slide2[data-i="${i}"] .pvin`); if (!el) return;
  await CS.mount(el, A.slides[i], await slideCtx(i));
}
function readSlides() {
  const box = $('#slides'); if (!box) return;
  A.slides = [...box.querySelectorAll('.slide2')].map((el, i) => {
    const o = { ...(A.slides[i] || {}) };
    el.querySelectorAll('[data-s]').forEach(x => {
      const k = x.dataset.s, v = x.value;
      o[k] = k === 'bullets' || k === 'chips' ? CS.lines(v) : k === 'cards' ? CS.cards(v)
        : k === 'streets' ? CS.lines(v).map(l => { const [label, ...rest] = l.split('|'); return { label: (label || '').trim(), text: rest.join('|').trim() } }) : v;
      if (k === 'streets') { o.cards = o.streets; delete o.streets }
    });
    if (i === 0) o.layout = 'portada';
    return o;
  });
}
async function saveSlides() { readSlides(); try { await api('/api/articles/' + A.id, { method: 'PUT', body: JSON.stringify({ slides: A.slides }) }); toast('Carrusel guardado') } catch (e) { toast(e.message) } }
async function renderPng(i) {
  readSlides();
  await document.fonts.ready;
  const host = document.createElement('div'); host.style.cssText = 'position:fixed;left:-20000px;top:0;width:1080px;height:1350px;overflow:hidden';
  document.body.appendChild(host);
  try { const el = await CS.mount(host, A.slides[i], await slideCtx(i)); return await CS.toPng(el) } finally { host.remove() }
}
async function generatePngs() {
  const b = $('#genPngs'); b.disabled = true;
  try {
    const out = [];
    for (let i = 0; i < A.slides.length; i++) { b.innerHTML = `<span class="spin"></span> ${i + 1}/${A.slides.length}`; out.push(await renderPng(i)) }
    await api('/api/articles/' + A.id, { method: 'PUT', body: JSON.stringify({ slides: A.slides }) });
    const r = await post(`/api/articles/${A.id}/carousel/png`, { slides: out });
    A.media = r.media; drawImages(); toast('Carrusel listo: está en Imágenes y en el ZIP');
  } catch (e) { toast('No se pudo generar: ' + e.message) }
  b.disabled = false; b.textContent = 'Generar imágenes del carrusel';
}

function drawImages() {
  const box = $('#imgs'), media = A.media || [], n = (A.slides || []).length;
  const kinds = Object.keys(KIND).filter(k => media.some(m => m.kind === k));
  if (!media.length) { box.innerHTML = '<p class="muted small">Sin imágenes todavía. Busca en internet, sube las tuyas o saca las del expediente.</p>'; return }
  box.innerHTML = kinds.map(k => `<div class="sect">${KIND[k]} <span class="muted small">${media.filter(m => m.kind === k).length}</span></div><div class="imgs">${media.filter(m => m.kind === k).map(m => `
    <div class="tile ${m.selected ? 'sel' : ''}" data-m="${m.id}">
      <img alt="${esc(m.caption || '')}" loading="lazy" ${m.local_path ? `data-local="${m.id}"` : `src="${safeUrl(m.url)}" referrerpolicy="no-referrer"`} onerror="this.closest('.tile').style.opacity=.35">
      <button class="chk" data-sel title="Marcar para el carrusel">${m.selected ? '✓' : ''}</button>
      ${m.caption ? `<span class="cap" title="${esc(m.caption)}">${esc(m.caption)}</span>` : ''}
      <div class="bar"><select data-slide title="Diapositiva"><option value="">Diapo</option>${Array.from({ length: n }, (_, i) => `<option value="${i + 1}" ${m.slide === i + 1 ? 'selected' : ''}>${i + 1}</option>`).join('')}</select>
        <button data-dl title="Descargar">⬇</button>${m.source ? `<button data-src title="Ver origen">↗</button>` : ''}<button data-rm title="Quitar">✕</button></div></div>`).join('')}</div>`).join('');
  box.querySelectorAll('.tile').forEach(t => {
    const id = +t.dataset.m, m = media.find(x => x.id === id);
    t.querySelector('[data-sel]').onclick = async () => { m.selected = m.selected ? 0 : 1; await post(`/api/articles/${A.id}/media/${id}`, { selected: m.selected }); t.classList.toggle('sel', !!m.selected); t.querySelector('[data-sel]').textContent = m.selected ? '✓' : '' };
    t.querySelector('[data-slide]').onchange = async e => { m.slide = e.target.value ? +e.target.value : null; if (m.slide && !m.selected) { m.selected = 1; t.classList.add('sel'); t.querySelector('[data-sel]').textContent = '✓' } await post(`/api/articles/${A.id}/media/${id}`, { slide: m.slide, selected: m.selected }); A.slides.forEach((_, j) => preview(j)) };
    t.querySelector('[data-dl]').onclick = () => download(`/api/media/${id}/file`, `area_${A.id}_${id}.jpg`);
    t.querySelector('[data-src]') && (t.querySelector('[data-src]').onclick = () => window.open(/^https?:/.test(m.source) ? m.source : m.url, '_blank', 'noopener'));
    t.querySelector('[data-rm]').onclick = async () => { await post(`/api/articles/${A.id}/media/${id}`, { delete: true }); A.media = A.media.filter(x => x.id !== id); drawImages() };
  });
  (async () => { for (const img of box.querySelectorAll('[data-local]')) { try { img.src = await blobUrl('/api/media/' + img.dataset.local + '/file') } catch { } } })();
}

async function drawCanva() {
  const box = $('#canvaBox');
  let t; try { t = await api('/api/canva/template') } catch { t = {} }
  const pages = (A.media || []).filter(m => m.kind === 'canva');
  box.innerHTML = `<div class="row wrap"><h3 class="grow">Canva</h3>
    ${A.canva_url ? `<a class="btn small" href="${safeUrl(A.canva_url)}" target="_blank" rel="noopener">Abrir en Canva</a><button class="btn small" id="canvaExport">Actualizar PNG desde Canva</button>` : ''}
    <button class="btn small primary" id="canvaMake" ${t.connected && t.template_id ? '' : 'disabled'}>${A.canva_url ? 'Volver a crear el carrusel' : 'Crear carrusel en Canva'}</button></div>
    ${!t.ready ? '<p class="small muted">Canva no está configurado en el servidor (CANVA_CLIENT_ID, CANVA_CLIENT_SECRET y PUBLIC_BASE_URL). Mientras tanto copia los textos y descarga las imágenes.</p>'
      : !t.connected ? '<p class="small muted">Conecta Canva en Ajustes.</p>' : !t.template_id ? '<p class="small muted">Falta la plantilla del carrusel: pégala en Ajustes → Canva.</p>'
        : `<p class="small muted">Se rellena la plantilla con cada diapositiva y las imágenes marcadas (por su número de diapositiva o en orden).</p>`}
    ${A.canva_error ? `<p class="error small">${esc(A.canva_error)}</p>` : ''}
    ${pages.length ? `<p class="small">${pages.length} páginas exportadas: están arriba en «Carrusel Canva» y en el ZIP.</p>` : ''}`;
  $('#canvaMake').onclick = async e => { e.target.disabled = true; e.target.innerHTML = '<span class="spin"></span> Creando en Canva…'; try { await saveAll(true); const r = await post(`/api/articles/${A.id}/canva`); A = { ...A, ...r.article }; drawImages(); drawCanva(); toast((r.missing_slides || []).length ? `Creado en Canva. Las diapositivas ${r.missing_slides.join(', ')} no tienen página en tu plantilla: descárgalas con «Generar imágenes del carrusel»` : 'Carrusel creado en Canva') } catch (err) { toast(err.message); drawCanva() } };
  $('#canvaExport') && ($('#canvaExport').onclick = async e => { e.target.disabled = true; try { const r = await post(`/api/articles/${A.id}/canva/export`); A = { ...A, ...r }; drawImages(); toast('PNG actualizados') } catch (err) { toast(err.message) } e.target.disabled = false });
}

/* ---------- Ajustes ---------- */
async function settingsView(v) {
  const [h, s, e, t, brandState] = await Promise.all([api('/api/health'), api('/api/sources'), api('/api/efemerides'), api('/api/canva/template').catch(() => ({})), api('/api/brand').catch(() => ({}))]);
  const blocks = s.blocks;
  v.innerHTML = `<div class="stack">
    <section class="card stack"><h3>Estado</h3>
      <p class="small">Redacción: <b>${h.draft_mode === 'ai' ? 'con IA (' + esc(h.ai_provider) + ')' : 'sin IA: borradores desde la fuente'}</b>. ${h.draft_mode === 'ai' ? '' : 'Añade GEMINI_API_KEY (gratis) en Railway para redactar con IA.'}</p>
      <div class="row wrap"><button class="btn small" id="aiCheck">Probar IA</button><button class="btn small" id="logout">Cerrar sesión</button></div><div id="aiOut" class="small"></div></section>
    <section class="card stack"><h3>Logos de Área para los carruseles</h3>
      <p class="small muted">PNG con fondo transparente. El blanco va en la pestaña roja de la portada; el de color, arriba a la derecha en las diapositivas claras. Sin logo se escribe «Área · Campo de Gibraltar».</p>
      ${['blanco', 'color'].map(k => `<div class="row wrap"><b class="grow">Logo ${k}: ${brandState[k] ? '<span class="ok">subido</span>' : '<span class="muted">sin subir</span>'}</b>
        <label class="btn small" style="display:inline-flex;align-items:center">Subir<input type="file" accept="image/png,image/*" data-logo="${k}" hidden></label>${brandState[k] ? `<button class="btn small" data-logodel="${k}">Quitar</button>` : ''}</div>`).join('')}</section>
    <section class="card stack"><h3>Canva · plantilla del carrusel</h3>
      ${!t.ready ? '<p class="small">Falta configurar en Railway: <span class="code">CANVA_CLIENT_ID</span>, <span class="code">CANVA_CLIENT_SECRET</span> y <span class="code">PUBLIC_BASE_URL</span>.</p>'
      : `<p class="small">${t.connected ? '<span class="ok">Conectado</span>' : 'No conectado'} · URL de retorno: <span class="code">${esc(t.callback)}</span></p><div class="row"><button class="btn small" id="canvaConnect">${t.connected ? 'Reconectar' : 'Conectar'} Canva</button></div>`}
      <label>Enlace de la plantilla de marca<input id="tplLink" value="${esc(t.template_id || '')}" placeholder="https://www.canva.com/brand-templates/…"></label>
      <div class="row"><button class="btn small" id="tplSave">Guardar plantilla</button></div>
      ${t.error ? `<p class="error small">${esc(t.error)}</p>` : ''}
      ${(t.fields || []).length ? `<p class="small">Campos encontrados: ${t.fields.map(f => `<span class="code" style="${f.role ? '' : 'opacity:.5'}">${esc(f.name)}${f.role ? ' → ' + f.role + (f.slide ? ' ' + f.slide : '') : ''}</span>`).join(' ')}</p>` : ''}
      ${(t.typed || []).length ? `<p class="small">Páginas reconocidas: ${t.typed.map(x => `<span class="code">${esc(x)}</span>`).join(' ')}</p>` : ''}
      <label>Orden de las páginas de la plantilla (una por línea o separadas por comas)<textarea id="tplPages" rows="3" placeholder="PORTADA, LISTA, FICHA, CAJA…">${esc(t.pages || (t.page_order || []).join(', '))}</textarea></label>
      <div class="row"><button class="btn small" id="tplPagesSave">Guardar orden</button></div>
      <details class="peek"><summary>Cómo nombrar los cuadros en Canva (plantilla por tipos)</summary>
        <p class="small">Una página por tipo de diapositiva. En cada página, abre <b>Aplicaciones → Autocompletar (Bulk/Data autofill)</b> y pon a cada cuadro de texto o foto el nombre de la lista. Los gráficos (barras rojas, flechas, cajas, degradados, marco) son fijos: la app solo pone textos y fotos. Si un tipo puede salir dos veces, duplica la página y numera el tipo: <span class="code">LISTA2_TITULO</span>. No hace falta tener todos los tipos: lo que falte se puede descargar desde «Generar imágenes del carrusel».</p>
        ${(t.guide || []).map(g => `<p class="small" style="margin:6px 0"><b>${esc((CS.LAYOUTS[g.layout] || {}).label || g.layout)}</b><br>${g.fields.map(f => `<span class="code">${esc(f)}</span>`).join(' ')}</p>`).join('')}
        <p class="small muted">Antigua (numerada): <span class="code">TITULO_1</span>, <span class="code">TEXTO_1</span>, <span class="code">FOTO_1</span>… por número de diapositiva.</p></details></section>
    <section class="card stack"><h3>Fuentes del radar (${s.sources.length})</h3>
      ${blocks.filter(b => b !== 'Efemérides').map(b => `<div class="sect">${esc(b)}</div>${s.sources.filter(x => x.block === b).map(x => srcRow(x)).join('') || '<p class="small muted">Ninguna</p>'}`).join('')}
      <details class="peek"><summary>Añadir fuente</summary><div class="stack" style="margin-top:8px">
        <input id="nsName" placeholder="Nombre"><input id="nsUrl" placeholder="https://… (RSS, web o búsqueda de Google News)">
        <div class="row"><select id="nsKind"><option value="rss">RSS / Google News</option><option value="html">Página web</option><option value="placsp">ATOM Plataforma de Contratación</option><option value="gobierto">Gobierto (adjudicador)</option></select>
        <select id="nsBlock">${blocks.filter(b => b !== 'Efemérides').map(b => `<option>${esc(b)}</option>`).join('')}</select></div>
        <button class="btn small primary" id="nsAdd">Añadir</button></div></details></section>
    <section class="card stack"><h3>Diario Área y competencia</h3><p class="small muted">Sirven para no repetir lo que ya ha publicado Área y para saber si algo es exclusiva.</p>
      ${s.press.map(x => `<div class="src ${x.active ? '' : 'off'}"><div class="grow"><b>${esc(x.name)}</b> <span class="tag ${x.role === 'area' ? 'area' : 'comp'}">${x.role === 'area' ? 'Área' : 'Competencia'}</span><small>${x.last_error ? '⚠ ' + esc(x.last_error) : (x.items_seen || 0) + ' titulares la última vez'}</small></div><button class="btn tiny" data-ptog="${x.id}">${x.active ? 'Pausar' : 'Activar'}</button></div>`).join('')}
      <details class="peek"><summary>Añadir medio</summary><div class="stack" style="margin-top:8px"><input id="npName" placeholder="Nombre"><input id="npUrl" placeholder="RSS del medio o búsqueda de Google News"><select id="npRole"><option value="competitor">Competencia</option><option value="area">Diario Área</option></select><button class="btn small primary" id="npAdd">Añadir</button></div></details></section>
    <section class="card stack"><h3>Efemérides</h3>
      ${e.all.map(x => `<div class="src ${x.active ? '' : 'off'}"><div class="grow"><b>${x.day}/${x.month}${x.year ? '/' + x.year : ''}</b> · ${esc(x.title)} ${x.verified ? '' : '<span class="tag upd">sin verificar</span>'}<small>${esc(x.note || '')}</small></div>
        ${x.verified ? '' : `<button class="btn tiny" data-ever="${x.id}">Verificada</button>`}<button class="btn tiny" data-edel="${x.id}">✕</button></div>`).join('')}
      <details class="peek"><summary>Añadir efeméride</summary><div class="stack" style="margin-top:8px"><div class="row"><input id="eDay" type="number" min="1" max="31" placeholder="Día"><input id="eMonth" type="number" min="1" max="12" placeholder="Mes"><input id="eYear" type="number" placeholder="Año"></div>
        <input id="eTitle" placeholder="Qué pasó"><input id="eNote" placeholder="Nota / fuente"><input id="eTowns" placeholder="Municipios"><label class="row" style="font-weight:600"><input type="checkbox" id="eVer" style="width:auto"> Contrastada</label><button class="btn small primary" id="eAdd">Añadir</button></div></details></section>
    <section class="card stack"><h3>Actividad</h3><div id="act" class="small muted"></div></section></div>`;
  $('#logout').onclick = logout;
  v.querySelectorAll('[data-logo]').forEach(x => x.onchange = async () => { const fd = new FormData(); fd.append('file', x.files[0]); try { await api('/api/brand/logo/' + x.dataset.logo, { method: 'POST', body: fd }); toast('Logo guardado'); render() } catch (err) { toast(err.message) } });
  v.querySelectorAll('[data-logodel]').forEach(x => x.onclick = async () => { await api('/api/brand/logo/' + x.dataset.logodel, { method: 'POST', body: new FormData() }); render() });
  $('#aiCheck').onclick = async () => { $('#aiOut').innerHTML = '<span class="spin"></span>'; try { const r = await api('/api/ai/check'); $('#aiOut').innerHTML = r.providers.map(p => `${esc(p.provider_name)}: ${p.configured ? (p.ok ? '<span class="ok">funciona</span>' : esc(p.message || 'error')) : 'sin clave'}`).join('<br>') } catch (err) { $('#aiOut').textContent = err.message } };
  $('#canvaConnect') && ($('#canvaConnect').onclick = async () => { try { location.href = (await api('/api/canva/connect')).url } catch (err) { toast(err.message) } });
  $('#tplPagesSave').onclick = async () => { try { await api('/api/canva/template', { method: 'PUT', body: JSON.stringify({ pages: $('#tplPages').value }) }); toast('Orden guardado'); render() } catch (err) { toast(err.message) } };
  $('#tplSave').onclick = async () => { try { await api('/api/canva/template', { method: 'PUT', body: JSON.stringify({ template: $('#tplLink').value }) }); toast('Plantilla guardada'); render() } catch (err) { toast(err.message) } };
  v.querySelectorAll('[data-tog]').forEach(b => b.onclick = async () => { await post(`/api/sources/${b.dataset.tog}/toggle`); render() });
  v.querySelectorAll('[data-ptog]').forEach(b => b.onclick = async () => { await post(`/api/sources/${b.dataset.ptog}/toggle?press=1`); render() });
  v.querySelectorAll('[data-chk]').forEach(b => b.onclick = async () => { b.innerHTML = '<span class="spin"></span>'; const r = await post(`/api/sources/${b.dataset.chk}/check`); b.textContent = r.ok ? `${r.items_seen} ✓` : 'Error'; if (!r.ok) toast(r.error) });
  v.querySelectorAll('[data-sdel]').forEach(b => b.onclick = async () => { if (confirm('¿Borrar la fuente?')) { await api(`/api/sources/${b.dataset.sdel}`, { method: 'DELETE' }); render() } });
  $('#nsAdd').onclick = async () => { try { await post('/api/sources', { name: $('#nsName').value, url: $('#nsUrl').value, kind: $('#nsKind').value, block: $('#nsBlock').value }); render() } catch (err) { toast(err.message) } };
  $('#npAdd').onclick = async () => { try { await post('/api/sources', { press: 1, name: $('#npName').value, url: $('#npUrl').value, role: $('#npRole').value }); render() } catch (err) { toast(err.message) } };
  v.querySelectorAll('[data-ever]').forEach(b => b.onclick = async () => { await post(`/api/efemerides/${b.dataset.ever}`, { verified: 1 }); render() });
  v.querySelectorAll('[data-edel]').forEach(b => b.onclick = async () => { if (confirm('¿Borrar la efeméride?')) { await post(`/api/efemerides/${b.dataset.edel}`, { delete: 1 }); render() } });
  $('#eAdd').onclick = async () => { try { await post('/api/efemerides', { day: $('#eDay').value, month: $('#eMonth').value, year: $('#eYear').value, title: $('#eTitle').value, note: $('#eNote').value, towns: $('#eTowns').value, verified: $('#eVer').checked }); render() } catch (err) { toast(err.message) } };
  try { const act = await api('/api/activity'); $('#act').innerHTML = act.slice(0, 25).map(a => `<div>${ago(a.created_at.replace(' ', 'T') + 'Z')} · ${esc(a.message)}</div>`).join('') || 'Sin actividad' } catch { }
}
function srcRow(x) {
  return `<div class="src ${x.active ? '' : 'off'}"><div class="grow"><b>${esc(x.name)}</b>${x.official ? ' <span class="tag">oficial</span>' : ''}<small>${x.last_error ? '⚠ ' + esc(x.last_error) : x.last_checked_at ? `${x.items_seen} leídas · ${x.items_added} nuevas la última vez` : 'Sin leer todavía'}</small></div>
    <button class="btn tiny" data-chk="${x.id}">Probar</button><button class="btn tiny" data-tog="${x.id}">${x.active ? 'Pausar' : 'Activar'}</button><button class="btn tiny" data-sdel="${x.id}">✕</button></div>`;
}

if (token) start(); else logout();
