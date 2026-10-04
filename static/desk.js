/* InfoLinense Desk 2.4 — Redactar, prioridades, agenda, fotos, carrusel y panel final.
   Se carga después de app.js y sustituye algunas vistas sin romper las existentes. */
titles.agenda=['Agenda','Plan de publicación. Las horas que cambies a mano se respetan al entrar noticias nuevas.'];
titles.final=['Listo para publicar','Copia el texto y descarga la imagen. La publicación es siempre manual.'];
const PRIO=[['urgent','Urgente'],['today','Hoy'],['this_week','Esta semana'],['future','Futura'],['no_interest','No interesa']];
const PRIO_LABEL=Object.fromEntries(PRIO);
const polling={};
const draftCache={};

function fmtDate(v){if(!v)return '';const d=new Date(v);if(isNaN(d))return esc(v);return d.toLocaleString('es-ES',{timeZone:'Europe/Madrid',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'})}
function localInput(v){return v?String(v).slice(0,16):''}
function specialLabel(n){const t=`${n.title} ${n.excerpt||''} ${n.source_name||''}`.toLowerCase();if(n.source_kind==='procurement'||/licitaci|contrataci|adjudicaci/.test(t))return 'Licitación';if(n.source_kind==='bop'||/edicto/.test(t))return 'Edicto';if(/exclusiv/.test(t))return 'Exclusiva';return ''}
function jparse(s,d){try{return JSON.parse(s||'')??d}catch{return d}}

window.newsRow=function(n){
  const special=specialLabel(n);
  const hasDraft=!!n.article_id;
  const label=hasDraft?(n.article_status==='review_ready'?'En revisión':n.article_status==='approved'?'Aprobada':'Borrador'):({new:'Nueva',researched:'Investigada',draft:'Borrador',needs_config:'Pendiente'}[n.status]||n.status);
  const prio=n.editorial_priority&&n.editorial_priority!=='undecided'?n.editorial_priority:'';
  const chips=PRIO.map(([k,t])=>`<button class="chip ${prio===k?'on':''} ${k==='no_interest'?'chip-no':''}" onclick="setPriority(${n.id},'${k}')">${t}</button>`).join('');
  const plan=prio&&prio!=='no_interest'?`<div class="plan">Agenda: <input type="datetime-local" id="plan-${n.id}" value="${attr(localInput(n.planned_at))}" onchange="setPlanTime(${n.id},'${prio}')"> ${n.plan_locked?'<span class="pill lock">hora fijada por ti</span>':'<span class="meta">automática</span>'} ${n.plan_reason?`<span class="meta">${esc(n.plan_reason)}</span>`:''}</div>`:'';
  if(n.work_state==='working'&&!polling[n.id])setTimeout(()=>pollWrite(n.id),50);
  return `<div class="news" id="candidate-${n.id}"><div class="score">${n.score}</div><div class="news-main">
    <div><span class="pill">${esc(label)}</span>${special?`<span class="pill special">${special}</span>`:''}<span class="meta">${esc(n.source_name||'')}${n.published_at?' · '+fmtDate(n.published_at):''}</span></div>
    <h3>${esc(n.title)}</h3>
    ${n.local_angle?`<div class="meta">Por qué importa aquí: ${esc(n.local_angle)}</div>`:''}
    <div class="meta">${n.url?`<a href="${safeUrl(n.url)}" target="_blank" rel="noopener noreferrer">Ver fuente original</a>`:''}</div>
    <div class="chips">${chips}</div>${plan}
    ${researchNotes(n)}
    <div id="work-${n.id}"></div><div class="draft-editor" id="draft-${n.id}"></div></div>
    <div class="actions"><button class="primary" id="write-${n.id}" onclick="writeStory(${n.id})">${hasDraft?'Abrir borrador':'Redactar'}</button>
    <button class="ghost" onclick="archiveCandidate(${n.id})">Descartar</button></div></div>`;
};

window.setPriority=async(id,p)=>{try{await api(`/api/candidates/${id}/triage`,{method:'POST',body:JSON.stringify({priority:p})});toast(p==='no_interest'?'Marcada como no interesa':'Prioridad: '+PRIO_LABEL[p]);await render()}catch(e){toast(e.message)}};
window.setPlanTime=async(id,p)=>{const v=$(`#plan-${id}`).value;if(!v)return;try{await api(`/api/candidates/${id}/triage`,{method:'POST',body:JSON.stringify({priority:p,planned_at:v+':00'})});toast('Hora guardada; no se moverá automáticamente');await render()}catch(e){toast(e.message)}};

function workBox(id,html){const box=$(`#work-${id}`);if(box)box.innerHTML=html}
function setWriteButton(id,busy){const b=$(`#write-${id}`);if(!b)return;b.disabled=busy;b.classList.toggle('loading',busy);if(busy)b.textContent='Trabajando…'}
function showWriteError(id,msg){setWriteButton(id,false);const b=$(`#write-${id}`);if(b)b.textContent='Redactar';workBox(id,`<div class="notice warn"><b>No se pudo redactar.</b> ${esc(msg)}<div class="actions" style="margin-top:8px"><button class="primary" onclick="writeStory(${id})">Reintentar</button></div></div>`)}

window.writeStory=async id=>{
  if(polling[id])return;
  setWriteButton(id,true);workBox(id,'<div class="notice working"><span class="spinner"></span> Preparando…</div>');
  try{
    const r=await api(`/api/candidates/${id}/write?wait=45`,{method:'POST'});
    if(r.state==='working'){workBox(id,`<div class="notice working"><span class="spinner"></span> ${esc(r.step_text||'Trabajando…')}</div>`);return pollWrite(id)}
    finishWrite(id,r);
  }catch(e){showWriteError(id,e.message)}
};
async function pollWrite(id){
  polling[id]=true;setWriteButton(id,true);
  try{
    for(let i=0;i<120;i++){
      const s=await api(`/api/candidates/${id}/write`);
      if(s.state==='working'){workBox(id,`<div class="notice working"><span class="spinner"></span> ${esc(s.step_text||'Trabajando…')}</div>`);await new Promise(r=>setTimeout(r,3000));continue}
      if(s.state==='error'){showWriteError(id,s.error?.detail||s.error?.message||'Error desconocido');return}
      if(s.state==='done'){finishWrite(id,s);return}
      showWriteError(id,'El trabajo se detuvo sin terminar.');return;
    }
    showWriteError(id,'Está tardando demasiado. Pulsa Reintentar.');
  }catch(e){showWriteError(id,e.message)}finally{delete polling[id]}
}
function finishWrite(id,r){setWriteButton(id,false);const b=$(`#write-${id}`);if(b)b.textContent='Abrir borrador';workBox(id,r.action==='drafted'?'<div class="notice ok">Borrador creado.</div>':'');openDraft(id,r.article)}

window.showDraft=async id=>{try{openDraft(id,await api(`/api/candidates/${id}/draft`))}catch(e){toast(e.message)}};

function openDraft(cid,a){
  if(!a)return;draftCache[a.id]=a;
  const box=$(`#draft-${cid}`)||$(`#final-edit-${a.id}`);if(!box)return;
  const options=jparse(a.headline_options_json,[]);const missing=jparse(a.missing_data_json,[]);const srcs=jparse(a.sources_json,[]);
  box.innerHTML=`<div class="card draft-card">
    <div class="meta">${a.ai_provider?'Redactado con '+(a.ai_provider==='anthropic'?'Claude':'ChatGPT'):'Borrador basado solo en la fuente original'} · Revisa los datos antes de publicar.</div>
    <label>Titular<input class="draft-input" id="ah-${a.id}" value="${attr(a.headline||'')}"></label>
    ${options.length?`<div class="meta">Titulares alternativos (pulsa para usar uno):</div><div class="alt-headlines">${options.map((h,i)=>`<button class="alt" onclick="useHeadline(${a.id},${i})">${esc(h)}</button>`).join('')}</div>`:''}
    <button class="ghost small" onclick="newHeadlines(${cid},${a.id},this)">${options.length?'Proponer otros 7 titulares':'Proponer 7 titulares'}</button>
    <label>Entradilla<textarea class="draft-input short" id="as-${a.id}">${esc(a.subtitle||'')}</textarea></label>
    <label>Cuerpo <span class="counter" id="ac-${a.id}">${(a.body||'').length}/2200</span><textarea class="draft-input tall" id="ab-${a.id}" maxlength="2200" oninput="$('#ac-${a.id}').textContent=this.value.length+'/2200'">${esc(a.body||'')}</textarea></label>
    <label>Texto corto para la imagen<textarea class="draft-input short" id="ag-${a.id}" maxlength="240">${esc(a.graphic_summary||'')}</textarea></label>
    ${missing.length?`<div class="notice warn"><b>Datos que faltan por confirmar:</b><ul>${missing.map(x=>`<li>${esc(typeof x==='string'?x:JSON.stringify(x))}</li>`).join('')}</ul></div>`:''}
    ${srcs.length?`<div class="meta"><b>Fuentes:</b> ${srcs.map(s=>`<a href="${safeUrl(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.name||s.url)}</a>`).join(' · ')}</div>`:''}
    <div class="photo-block" id="photo-${a.id}">${photoSummary(a)}</div>
    <div class="carousel-block" id="carousel-${a.id}">${a.carousel_suitable?`<div class="notice">Esta noticia puede funcionar como carrusel: ${esc(a.carousel_reason||'')}</div>`:''}<button class="ghost small" onclick="analyseCarousel(${a.id},this)">Analizar carrusel</button></div>
    <div class="actions"><button class="secondary" onclick="saveDraftV2(${cid},${a.id},false)">Guardar</button>${a.status==='draft'?`<button class="primary" onclick="saveDraftV2(${cid},${a.id},true)">Guardar y enviar a Revisión</button>`:''}</div></div>`;
  box.scrollIntoView({behavior:'smooth',block:'nearest'});
}
window.useHeadline=(aid,i)=>{const h=jparse(draftCache[aid]?.headline_options_json,[])[i];if(h){$(`#ah-${aid}`).value=h;toast('Titular cambiado; pulsa Guardar')}};
window.newHeadlines=async(cid,aid,btn)=>{btn.disabled=true;btn.textContent='Pensando titulares…';try{const r=await api(`/api/articles/${aid}/headlines`,{method:'POST'});const a={...draftCache[aid],headline:$(`#ah-${aid}`).value,subtitle:$(`#as-${aid}`).value,body:$(`#ab-${aid}`).value,graphic_summary:$(`#ag-${aid}`).value,headline_options_json:JSON.stringify(r.headlines)};openDraft(cid,a)}catch(e){toast(e.message);btn.disabled=false;btn.textContent='Reintentar titulares'}};
window.saveDraftV2=async(cid,aid,submit)=>{try{const a=await api(`/api/articles/${aid}`,{method:'PUT',body:JSON.stringify({headline:$(`#ah-${aid}`).value,subtitle:$(`#as-${aid}`).value,body:$(`#ab-${aid}`).value,graphic_summary:$(`#ag-${aid}`).value})});draftCache[aid]={...draftCache[aid],...a};if(submit){await api(`/api/candidates/${cid}/submit`,{method:'POST'});toast('Enviado a Revisión');setView('review')}else toast('Guardado')}catch(e){toast(e.message)}};

/* ---------- Fotos ---------- */
function photoSummary(a){
  const credit=[a.image_source,a.image_author,a.image_license].filter(Boolean).map(esc).join(' · ');
  return `<div class="notice ${a.image_url?'':'warn'}"><b>${a.image_url?'Foto elegida':'Sin foto todavía'}</b>${a.image_url?`<br><small>Procedencia: ${credit||'sin datos'}</small>`:''}
  <div class="actions" style="margin-top:8px"><button class="ghost small" onclick="photoGallery(${a.id})">Fotos de las fuentes e internet</button><button class="ghost small" onclick="photoUploadForm(${a.id})">Subir mi foto</button></div></div><div id="gallery-${a.id}"></div>`;
}
window.photoGallery=async(aid,refresh=false,q='')=>{const box=$(`#gallery-${aid}`);box.innerHTML='<div class="notice working"><span class="spinner"></span> Buscando fotos…</div>';try{const imgs=q?await api(`/api/articles/${aid}/photos?q=${encodeURIComponent(q)}`):await api(`/api/articles/${aid}/photos${refresh?'?refresh=true':''}`);photoCache[aid]=imgs;
  box.innerHTML=`<div class="photo-search"><input id="pq-${aid}" placeholder="Buscar foto libre (Wikimedia Commons)" value="${attr(q)}"><button class="ghost small" onclick="photoGallery(${aid},false,$('#pq-${aid}').value)">Buscar</button><button class="ghost small" onclick="photoGallery(${aid},true)">Volver a buscar en la fuente</button></div>
  ${imgs.length?`<div class="gallery">${imgs.map((im,i)=>`<figure class="${im.publish_safe?'safe':'unsafe'}"><img src="${safeUrl(im.url)}" loading="lazy" alt=""><figcaption><span class="pill ${im.publish_safe?'ok':'warn'}">${im.publish_safe?'Uso permitido':'Requiere permiso'}</span><small>${esc(im.source||'')}${im.author?' · '+esc(im.author):''}<br>${esc(im.license||'')}</small><button class="secondary small" onclick="pickPhoto(${aid},${i})">Usar esta</button></figcaption></figure>`).join('')}</div>`:'<div class="notice warn">No se han encontrado fotos. Prueba otra búsqueda o sube una propia.</div>'}<div id="permit-${aid}"></div>`}catch(e){box.innerHTML=`<div class="notice warn">${esc(e.message)}</div>`}};
window.pickPhoto=async(aid,i)=>{const im=photoCache[aid]?.[i];if(!im)return;if(!im.publish_safe){$(`#permit-${aid}`).innerHTML=`<div class="card permit"><b>Esta foto no tiene una licencia libre comprobada.</b><p class="meta">Úsala solo si tienes permiso del autor o la fuente lo permite expresamente. Quedará indicado en la procedencia.</p><label>Permiso o licencia<input id="pl-${aid}" placeholder="Ej.: cedida por el Ayuntamiento para su difusión"></label><label>Autor (opcional)<input id="pa-${aid}" value="${attr(im.author||'')}"></label><label class="check"><input type="checkbox" id="pc-${aid}"> Tengo permiso para usarla</label><button class="primary small" onclick="confirmPhoto(${aid},${i})">Usar con permiso</button></div>`;return}
  await sendPhoto(aid,im)};
window.confirmPhoto=async(aid,i)=>{const im=photoCache[aid]?.[i];if(!$(`#pc-${aid}`).checked||!$(`#pl-${aid}`).value.trim()){toast('Indica el permiso y marca la casilla');return}await sendPhoto(aid,{...im,license:$(`#pl-${aid}`).value,author:$(`#pa-${aid}`).value,confirm_permission:true})};
async function sendPhoto(aid,im){try{await api(`/api/articles/${aid}/photo`,{method:'POST',body:JSON.stringify(im)});toast('Foto elegida');await refreshArticle(aid)}catch(e){toast(e.message)}}
window.photoUploadForm=aid=>{$(`#gallery-${aid}`).innerHTML=`<form class="card upload" onsubmit="uploadPhoto(event,${aid})"><label>Foto (JPG, PNG o WEBP, máx. 20 MB)<input type="file" name="file" accept="image/jpeg,image/png,image/webp" required></label><label>Procedencia<input name="source" value="Fotografía propia" required></label><label>Licencia o permiso<input name="license" value="Fotografía propia de InfoLinense" required></label><label>Autor<input name="author"></label><button class="primary small">Subir foto</button></form>`};
window.uploadPhoto=async(ev,aid)=>{ev.preventDefault();const fd=new FormData(ev.target);try{await api(`/api/articles/${aid}/photo/upload`,{method:'POST',body:fd});toast('Foto subida');await refreshArticle(aid)}catch(e){toast(e.message)}};
async function refreshArticle(aid){const a=await api(`/api/articles/${aid}`);draftCache[aid]={...draftCache[aid],...a};const el=$(`#photo-${aid}`);if(el)el.innerHTML=photoSummary(draftCache[aid]);if(current==='review')await review($('#view'));if(current==='final')await finalView($('#view'))}

/* ---------- Carrusel ---------- */
window.analyseCarousel=async(aid,btn)=>{btn.disabled=true;btn.textContent='Analizando…';try{const r=await api(`/api/articles/${aid}/carousel`,{method:'POST'});const box=$(`#carousel-${aid}`);
  box.innerHTML=r.suitable?`<div class="notice ok"><b>Sirve como carrusel</b> (${r.slides.length} diapositivas). ${esc(r.reason||'')}</div><ol class="slides">${r.slides.map(s=>`<li><b>${esc(s.title)}</b><br>${esc(s.text)}<br><small class="meta">Foto sugerida: ${esc(s.photo_query||'')}</small></li>`).join('')}</ol><button class="secondary small" onclick="designCarousel(${aid},this)">Crear diapositivas en Canva con la foto elegida</button><button class="ghost small" onclick="analyseCarousel(${aid},this)">Rehacer</button>`:`<div class="notice">No compensa hacer carrusel: ${esc(r.reason||'')}</div><button class="ghost small" onclick="analyseCarousel(${aid},this)">Volver a analizar</button>`}catch(e){toast(e.message);btn.disabled=false;btn.textContent='Reintentar análisis'}};
window.designCarousel=async(aid,btn)=>{const a=draftCache[aid]||await api(`/api/articles/${aid}`);if(!a.image_url){toast('Elige primero una foto con permiso');return}btn.disabled=true;btn.textContent='Creando en Canva…';try{await api(`/api/articles/${aid}/carousel/design`,{method:'POST',body:JSON.stringify({photo_urls:[a.image_url]})});toast('Carrusel listo; descárgalo en «Listo para publicar»')}catch(e){toast(e.message)}finally{btn.disabled=false;btn.textContent='Crear diapositivas en Canva con la foto elegida'}};

/* ---------- Agenda ---------- */
async function agenda(v){const s=await api('/api/schedule');const groups={};for(const it of s.items){const day=it.planned_at?String(it.planned_at).slice(0,10):'Sin hueco';(groups[day]=groups[day]||[]).push(it)}
  v.innerHTML=`<div class="notice">Exclusivas, edictos y licitaciones van primero dentro de cada prioridad. Al entrar noticias nuevas se recoloca solo lo automático; lo que fijes a mano no se mueve. <button class="ghost small" onclick="rebuildAgenda()">Recalcular ahora</button></div>${s.items.length?Object.entries(groups).map(([day,items])=>`<div class="section-title"><h2>${day==='Sin hueco'?day:new Date(day+'T12:00').toLocaleDateString('es-ES',{weekday:'long',day:'numeric',month:'long'})}</h2></div><div class="list">${items.map(agendaRow).join('')}</div>`).join(''):'<div class="empty">Aún no hay noticias con prioridad. Márcalas en Inicio o Radar (Urgente, Hoy, Esta semana o Futura).</div>'}`}
function agendaRow(it){const special=specialLabel(it);return `<div class="news agenda-row"><div class="time">${it.planned_at?String(it.planned_at).slice(11,16):'—'}</div><div><div><span class="pill">${esc(PRIO_LABEL[it.editorial_priority]||'')}</span>${special?`<span class="pill special">${special}</span>`:''}${it.plan_locked?'<span class="pill lock">fijada por ti</span>':''}${it.article_status?`<span class="pill">${{draft:'Borrador',review_ready:'En revisión',approved:'Aprobada',published:'Publicada'}[it.article_status]||it.article_status}</span>`:''}</div><h3>${esc(it.article_headline||it.title)}</h3><div class="meta">${esc(it.source_name||'')}${it.plan_reason?' · '+esc(it.plan_reason):''}</div></div><div class="actions"><input type="datetime-local" id="plan-${it.id}" value="${attr(localInput(it.planned_at))}" onchange="setPlanTime(${it.id},'${it.editorial_priority}')">${it.plan_locked?`<button class="ghost small" onclick="setPriority(${it.id},'${it.editorial_priority}')">Volver a automático</button>`:''}</div></div>`}
window.rebuildAgenda=async()=>{try{await api('/api/schedule/rebuild',{method:'POST'});toast('Agenda recalculada');render()}catch(e){toast(e.message)}};

/* ---------- Panel final ---------- */
async function finalView(v){const items=await api('/api/review');if(!items.length){v.innerHTML='<div class="empty">Cuando envíes noticias a Revisión aparecerán aquí listas para copiar y descargar.</div>';return}
  const kits=await Promise.all(items.map(a=>api(`/api/articles/${a.id}/kit`).catch(()=>null)));
  v.innerHTML=`<div class="notice">InfoLinense Desk no publica nada automáticamente. Copia el texto, descarga la imagen y publícalo tú.</div>`+kits.filter(Boolean).map(k=>`<article class="card final-card"><div class="final-grid"><div class="final-img">${k.image_url?`<img data-render="${k.id}" alt="Diseño final">`:k.photo_download_url?`<img data-photo="${k.id}" alt="Foto elegida"><small class="meta">Aún sin diseño de Canva: se muestra la foto.</small>`:'<div class="empty">Sin imagen</div>'}<div class="actions">${k.image_url?`<button class="primary" onclick="downloadRender(${k.id})">Descargar imagen (PNG Canva)</button>`:''}${k.photo_download_url?`<button class="secondary" onclick="downloadPhoto(${k.id})">Descargar foto original</button>`:''}${k.carousel_download_url?`<button class="secondary" onclick="downloadFile('${k.carousel_download_url}','infolinense-carrusel-${k.id}.zip')">Descargar carrusel (ZIP)</button>`:''}</div>${k.image_credit?`<p class="meta">Imagen: ${esc(k.image_credit)}</p>`:''}</div>
  <div><span class="pill">${esc(k.section||'')}</span><span class="pill">${k.status==='approved'?'Aprobada':'En revisión'}</span><h2>${esc(k.headline||'')}</h2><div class="textarea-read" id="copy-${k.id}">${esc(k.copy_text||'')}</div><div class="meta">${k.chars}/2200 caracteres en el cuerpo</div><div class="actions"><button class="primary" onclick="copyKit(${k.id})">Copiar texto</button><button class="ghost" onclick="copyKit(${k.id},true)">Copiar solo cuerpo</button>${!k.image_url?`<button class="ghost" onclick="setView('review')">Crear diseño en Canva</button>`:''}</div>${k.missing_data?.length?`<div class="notice warn"><b>Antes de publicar confirma:</b> ${k.missing_data.map(esc).join(' · ')}</div>`:''}${k.source_url?`<div class="meta">Fuente: <a href="${safeUrl(k.source_url)}" target="_blank" rel="noopener noreferrer">ver original</a></div>`:''}</div></div></article>`).join('');
  window._kits=Object.fromEntries(kits.filter(Boolean).map(k=>[k.id,k]));await loadRenders();
  for(const img of document.querySelectorAll('img[data-photo]')){try{const u=URL.createObjectURL(await (await request(`/api/articles/${img.dataset.photo}/photo/file`)).blob());renderUrls.push(u);img.src=u}catch{}}}
window.copyKit=async(id,bodyOnly)=>{const k=window._kits?.[id];if(!k)return;await navigator.clipboard.writeText(bodyOnly?k.text:k.copy_text);toast('Texto copiado')};
window.downloadFile=async(url,name)=>{try{const u=URL.createObjectURL(await (await request(url)).blob());const a=document.createElement('a');a.href=u;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(u),30000)}catch(e){toast(e.message)}};
window.downloadPhoto=id=>downloadFile(`/api/articles/${id}/photo/file`,`infolinense-${id}-foto.jpg`);

/* ---------- Vistas ---------- */
const _render=window.render;
window.render=async function(){const v=$('#view');if(current==='agenda'||current==='final'){v.innerHTML='<div class="empty">Cargando…</div>';try{return current==='agenda'?await agenda(v):await finalView(v)}catch(e){if(token)v.innerHTML=`<div class="empty">${esc(e.message)}</div>`}}return _render()};
const _settings=window.settings;
window.settings=async function(v){await _settings(v);const h=await api('/api/health');const metric=v.querySelector('.metric strong');if(metric)metric.textContent=h.ai_configured?(h.ai_provider==='anthropic'?'Claude':'ChatGPT')+(h.ai_fallback?.length?' (+ respaldo)':''):'Borrador de fuente';
  v.insertAdjacentHTML('afterbegin',`<div class="card" style="margin-bottom:16px"><h2>Comprobar la IA</h2><p class="meta">Hace una petición real y mínima a cada proveedor y muestra exactamente lo que responde (saldo, límite, clave…).</p><button class="secondary" onclick="checkAI(this)">Comprobar ahora</button><div id="ai-check"></div></div>`)};
window.checkAI=async btn=>{btn.disabled=true;btn.textContent='Comprobando…';try{const r=await api('/api/ai/check');$('#ai-check').innerHTML=r.providers.map(p=>`<div class="notice ${!p.configured?'':p.ok?'ok':'warn'}"><b>${esc(p.provider_name)}</b> ${p.active?'(principal)':'(respaldo)'} · modelo ${esc(p.model)} · búsqueda web ${p.web_search?'activada':'desactivada'}<br>${!p.configured?'Sin clave configurada':p.ok?'Funciona correctamente':`${esc(p.message)}<br><small>Respuesta real: HTTP ${esc(p.http_status??'—')} · ${esc(p.code||'')} · ${esc(p.provider_message||'')}</small>`}</div>`).join('')}catch(e){$('#ai-check').innerHTML=`<div class="notice warn">${esc(e.message)}</div>`}finally{btn.disabled=false;btn.textContent='Comprobar ahora'}};
