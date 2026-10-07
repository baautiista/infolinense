/* Carruseles de Área: tipos de diapositiva, vista previa y exportación a PNG (1080×1350) sin dependencias. */
const CS = (() => {
  const LAYOUTS = {
    portada: { label: 'Portada', fields: ['title', 'text'], help: 'Foto a sangre, titular en mayúsculas y entradilla sobre degradado rojo.' },
    lista: { label: 'Lista con barras', fields: ['title', 'text', 'bullets'], help: '«¿Por qué se actúa ahora?» / «Más sombra, más verde…»' },
    caja: { label: 'Caja roja con lista', fields: ['title', 'text', 'bullets'], help: '«¿Qué cambiará en la plaza?»: lista de lo que incluye.' },
    flujo: { label: 'Pasos + frases', fields: ['title', 'text', 'chips', 'bullets'], help: '«¿Qué se va a hacer?»: etiquetas con flechas y frases destacadas.' },
    cifra_lista: { label: 'Lista + cifra', fields: ['title', 'text', 'bullets', 'figure', 'figure_label'], help: 'Lista y una píldora roja con la cifra al pie.' },
    ficha: { label: 'Ficha de proyecto', fields: ['title', 'text', 'status', 'figure'], help: 'Proyecto con estado («Estado: Ejecutado») y la inversión en grande.' },
    pregunta: { label: 'Pregunta y respuesta', fields: ['title', 'text'], help: 'Fondo negro: duda del vecino y respuesta.' },
    anotada: { label: 'Foto anotada', fields: ['kicker', 'title', 'bullets'], help: 'Foto con 2-3 rótulos y flechas (obras, redes, elementos).' },
    mapa: { label: 'Texto + imagen/mapa', fields: ['title', 'text'], help: 'Texto arriba y una imagen insertada (mapa, plano, render).' },
    cifra: { label: 'Cifra gigante', fields: ['kicker', 'figure', 'figure_label', 'text'], help: 'Estilo claro: «+300 M€ de deuda amortizada».' },
    tarjetas: { label: 'Tarjetas con iconos', fields: ['kicker', 'title', 'cards'], help: 'Estilo claro: 2-4 tarjetas con icono, cifra y texto.' },
    mosaico: { label: 'Mosaico de fotos', fields: ['kicker', 'cards'], help: 'Estilo claro: 2-5 fotos con rótulo rojo y texto.' },
    documento: { label: 'Documento oficial', fields: ['kicker', 'title', 'text'], help: 'Estilo claro: «Lo certifica… Intervención» con el documento.' },
    calles: { label: 'Listado de calles', fields: ['title', 'text', 'streets'], help: 'Cada calle o lugar con lo que se hace en ella (hasta 6 por diapositiva).' },
  };
  const FIELD_LABEL = { kicker: 'Antetítulo', title: 'Título', text: 'Texto', bullets: 'Puntos (uno por línea)', chips: 'Etiquetas con flechas (una por línea)',
    figure: 'Cifra', figure_label: 'Texto de la cifra', status: 'Estado', cards: 'Tarjetas (una por línea: rótulo | cifra | texto | icono)', streets: 'Calles (una por línea: calle | qué se hace)' };
  const ICONS = {
    obras: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M19.4 13a7.6 7.6 0 0 0 0-2l2.1-1.6-2-3.5-2.5 1a7 7 0 0 0-1.7-1L15 3h-4l-.4 2.9a7 7 0 0 0-1.7 1l-2.5-1-2 3.5L6.6 11a7.6 7.6 0 0 0 0 2l-2.1 1.6 2 3.5 2.5-1a7 7 0 0 0 1.7 1L11 21h4l.4-2.9a7 7 0 0 0 1.7-1l2.5 1 2-3.5zM13 15.5A3.5 3.5 0 1 1 13 8.5a3.5 3.5 0 0 1 0 7z"/></svg>',
    familia: '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="7" cy="6" r="3"/><circle cx="17" cy="6" r="3"/><path d="M2 20v-6a4 4 0 0 1 4-4h2a4 4 0 0 1 4 4v6zM12 20v-6a4 4 0 0 1 4-4h2a4 4 0 0 1 4 4v6z"/></svg>',
    casa: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 3 2 11h3v10h5v-6h4v6h5V11h3z"/></svg>',
    calendario: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 2h2v2h6V2h2v2h3a1 1 0 0 1 1 1v15a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h3zm-2 7v10h14V9z"/></svg>',
    agua: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2s7 7.6 7 12.5A7 7 0 0 1 5 14.5C5 9.6 12 2 12 2z"/></svg>',
    arbol: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2a6 6 0 0 0-5.7 7.9A5 5 0 0 0 8 19h3v3h2v-3h3a5 5 0 0 0 1.7-9.1A6 6 0 0 0 12 2z"/></svg>',
    coche: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M5 11 7 5h10l2 6h1a1 1 0 0 1 1 1v6h-2v2h-3v-2H8v2H5v-2H3v-6a1 1 0 0 1 1-1zm2.4 0h9.2L15.5 7h-7zM6.5 16a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zm11 0a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3z"/></svg>',
    persona: '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="7" r="4"/><path d="M4 21v-2a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v2z"/></svg>',
    barco: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M11 2h2v3h4l2 7H5l2-7h4zM3 14h18l-2 5a5 5 0 0 1-3 1H8a5 5 0 0 1-3-1z"/></svg>',
    check: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z"/></svg>',
    luz: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M9 21h6v-1H9zm3-19a7 7 0 0 0-4 12.7V18h8v-3.3A7 7 0 0 0 12 2z"/></svg>',
  };
  const ICON_WORDS = { euro: 'euro', '€': 'euro', dinero: 'euro', obras: 'obras', servicios: 'obras', engranaje: 'obras', familia: 'familia', familias: 'familia',
    casa: 'casa', vivienda: 'casa', calendario: 'calendario', plazo: 'calendario', agua: 'agua', arbol: 'arbol', árbol: 'arbol', verde: 'arbol',
    coche: 'coche', aparcamiento: 'coche', persona: 'persona', empleo: 'persona', barco: 'barco', puerto: 'barco', check: 'check', luz: 'luz', alumbrado: 'luz' };
  const ARROW = '<svg width="96" height="60" viewBox="0 0 96 60"><path d="M6 30h78M62 8l24 22-24 22" stroke="#F20A0A" stroke-width="8" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const CHIP_ARROW = '<svg width="54" height="40" viewBox="0 0 54 40"><path d="M4 20h40M30 6l14 14-14 14" stroke="#F20A0A" stroke-width="6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const CURVE = ['<svg width="300" height="300" viewBox="0 0 300 300"><path d="M10 20C160 20 230 90 250 260M210 215l40 50 30-60" stroke="#fff" stroke-width="9" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    '<svg width="150" height="230" viewBox="0 0 150 230"><path d="M130 220C40 200 20 110 70 20M40 50l30-35 30 35" stroke="#fff" stroke-width="9" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    '<svg width="200" height="190" viewBox="0 0 200 190"><path d="M10 20c90 0 150 50 160 150M120 140l50 40 20-60" stroke="#fff" stroke-width="9" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>'];

  const esc = s => String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
  /* ==resaltado rojo==  ++resaltado azul++  **negrita** */
  const rich = s => esc(s).replace(/==(.+?)==/g, '<mark>$1</mark>').replace(/\+\+(.+?)\+\+/g, '<mark class="b">$1</mark>').replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
  const plain = s => String(s || '').replace(/==|\+\+|\*\*/g, '');
  const lines = v => Array.isArray(v) ? v.filter(Boolean) : String(v || '').split('\n').map(x => x.trim()).filter(Boolean);
  const cards = v => (Array.isArray(v) ? v : lines(v)).map(c => typeof c === 'object' ? c : (([label, figure, text, icon]) => ({ label, figure, text, icon }))(String(c).split('|').map(x => (x || '').trim()))).filter(c => c.label || c.text || c.figure);
  const bg = u => u ? `background-image:url('${u}')` : '';
  const tsize = (t, steps) => { const n = plain(t).length; for (const [max, px] of steps) if (n <= max) return px; return steps[steps.length - 1][1] };
  const word = (white) => `<div class="cs-word">Área<small>Campo de Gibraltar</small></div>`;
  const logo = (ctx, white) => (white ? ctx.logoWhite : ctx.logoColor) ? `<img src="${white ? ctx.logoWhite : ctx.logoColor}" alt="">` : word(white);
  const icon = name => { const k = ICON_WORDS[String(name || '').toLowerCase()] || (ICONS[name] ? name : 'euro'); return k === 'euro' ? '<span class="eur">€</span>' : ICONS[k] };

  function html(s, ctx) {
    const L = s.layout in LAYOUTS ? s.layout : 'lista', im = ctx.images || [];
    const dark = (inner, extra = '') => `<div class="cs cs-${L} ${extra}"><div class="cs-bg" style="${bg(im[0])}"></div><div class="cs-shade"></div><div class="cs-frame"></div>${inner}</div>`;
    const T = (t, steps = [[18, 120], [34, 108], [60, 96], [999, 84]]) => t ? `<h2 class="cs-t" style="--ts:${tsize(t, steps)}px">${rich(t)}</h2>` : '';
    const S = t => t ? `<p class="cs-s">${rich(t)}</p>` : '';
    const UL = (b, cls = 'bar') => lines(b).length ? `<ul class="cs-ul ${cls}">${lines(b).map(x => `<li>${rich(x)}</li>`).join('')}</ul>` : '';
    switch (L) {
      case 'portada':
        return `<div class="cs cs-portada"><div class="cs-bg" style="${bg(im[0])}"></div><div class="cs-fade"></div>
          <div class="cs-tab">${logo(ctx, true)}</div>
          <div class="cs-pbox fit"><h1 class="cs-pt" style="--ts:${tsize(s.title, [[40, 136], [70, 124], [100, 108], [999, 94]])}px">${rich(s.title)}</h1>${s.text ? `<p class="cs-ps">${rich(s.text)}</p>` : ''}</div>
          <div class="cs-arrow">${ARROW}</div></div>`;
      case 'lista':
        return dark(`<div class="cs-in fit">${T(s.title)}${S(s.text)}${UL(s.bullets)}</div>`);
      case 'caja':
        return dark(`<div class="cs-in fit">${T(s.title)}${S(s.text)}${lines(s.bullets).length ? `<ul class="cs-box">${lines(s.bullets).map(x => `<li>${rich(x)}</li>`).join('')}</ul>` : ''}</div>`);
      case 'flujo': {
        const ch = lines(s.chips);
        return dark(`<div class="cs-in fit">${T(s.title)}${S(s.text)}${ch.length ? `<div class="cs-chips">${ch.map((c, i) => `${i ? CHIP_ARROW : ''}<span class="cs-chip">${esc(plain(c))}</span>`).join('')}</div>` : ''}
          <div class="cs-par">${lines(s.bullets).map(x => `<p>${rich(x)}</p>`).join('')}</div></div>`);
      }
      case 'cifra_lista':
        return dark(`<div class="cs-in fit">${T(s.title)}${S(s.text)}${UL(s.bullets)}${s.figure ? `<div class="cs-pill">${esc(plain(s.figure))} ${rich(s.figure_label || '')}</div>` : ''}</div>`);
      case 'ficha':
        return dark(`<div class="cs-in fit" style="left:105px">${T(s.title, [[24, 116], [44, 104], [999, 90]])}${s.text ? `<p class="cs-bartext">${rich(s.text)}</p>` : ''}
          <div class="cs-row">${s.status ? `<span class="cs-status"><b>Estado:</b> ${esc(plain(s.status))}</span>` : '<span></span>'}${s.figure ? `<span class="cs-big" style="font-size:calc(var(--k)*${plain(s.figure).length <= 5 ? 110 : plain(s.figure).length <= 9 ? 88 : 70}px)">${esc(plain(s.figure))}</span>` : ''}</div></div>`);
      case 'pregunta':
        return `<div class="cs cs-pregunta cs-black"><div class="cs-frame"></div><div class="cs-in fit" style="left:90px">${T(s.title, [[30, 124], [60, 112], [999, 96]])}${s.text ? `<p class="cs-answer">${rich(s.text)}</p>` : ''}</div></div>`;
      case 'anotada': {
        const a = lines(s.bullets).slice(0, 3);
        return dark(`<div class="cs-in" style="top:150px;bottom:auto">${s.kicker ? `<p class="cs-ann-k">${rich(s.kicker)}</p>` : ''}<div class="cs-ann-t">${T(s.title, [[30, 104], [999, 86]])}</div></div>
          ${a.map((x, i) => `<p class="cs-ann a${i + 1}">${rich(x)}${CURVE[i]}</p>`).join('')}`);
      }
      case 'mapa':
        return dark(`<div class="cs-in fit">${T(s.title, [[20, 116], [40, 100], [999, 88]])}${s.text ? `<p class="cs-s" style="font-size:calc(var(--k)*44px);font-weight:700">${rich(s.text)}</p>` : ''}</div>
          <div class="cs-inset" style="${bg(im[1] || im[0])}"></div>`);
      case 'cifra': {
        const f = plain(s.figure), gs = f.length <= 6 ? 260 : f.length <= 9 ? 210 : 160;
        return `<div class="cs cs-light sky"><div class="cs-logo">${logo(ctx, false)}</div>${im[0] ? `<div class="cs-photo" style="${bg(im[0])};--fadebg:#F4F6F9"></div>` : ''}
          <div class="cs-li fit" style="bottom:${im[0] ? '600px' : '120px'}">${s.kicker ? `<div class="cs-kick">${esc(plain(s.kicker))}</div>` : ''}
          ${f ? `<div class="cs-giant" style="--gs:${gs}px">${esc(f)}</div>` : ''}${s.figure_label ? `<div class="cs-label">${esc(plain(s.figure_label))}</div>` : ''}
          ${s.text ? `<p class="cs-ctext">${rich(s.text)}</p>` : ''}</div></div>`;
      }
      case 'tarjetas': {
        const cs = cards(s.cards).slice(0, 4);
        return `<div class="cs cs-light pink"><div class="cs-logo">${logo(ctx, false)}</div>${im[0] ? `<div class="cs-photo" style="${bg(im[0])};height:24%;--fadebg:#FDF1F1"></div>` : ''}
          <div class="cs-li fit" style="bottom:${im[0] ? '300px' : '100px'}">${s.kicker ? `<div class="cs-kick">${esc(plain(s.kicker))}</div>` : ''}
          ${s.title ? `<h2 class="cs-lt" style="--ts:${tsize(s.title, [[30, 120], [50, 106], [999, 90]])}px">${rich(s.title)}</h2>` : ''}
          <div class="cs-cards">${cs.map(c => `<div class="cs-card"><div class="cs-ico">${icon(c.icon)}</div><div>${c.label ? `<h4>${esc(plain(c.label))}</h4>` : ''}
            ${c.figure && c.text && plain(c.text).length < 50 ? `<div class="two"><span class="fig">${esc(plain(c.figure))}</span><p>${rich(c.text)}</p></div>` : `${c.figure ? `<div class="fig">${esc(plain(c.figure))}</div>` : ''}${c.text ? `<p>${rich(c.text)}</p>` : ''}`}</div></div>`).join('')}</div></div></div>`;
      }
      case 'mosaico': {
        const cs = cards(s.cards).slice(0, 5), n = cs.length;
        return `<div class="cs cs-light" style="background:linear-gradient(180deg,#F4F6FA,#C9D8EE)"><div class="cs-logo">${logo(ctx, false)}</div>
          ${im[n] ? `<div class="cs-photo" style="${bg(im[n])};height:30%;--fadebg:#C9D8EE"></div>` : ''}
          <div class="cs-li fit" style="left:70px;right:55px;top:200px">${s.kicker ? `<div class="cs-kick">${esc(plain(s.kicker))}</div>` : '<div class="cs-kick"></div>'}
          <div class="cs-grid ${n === 2 ? 'n2' : ''}">${cs.map((c, i) => `<div class="cs-tile ${n === 4 || (n === 5 && i >= 3) ? 'w' : ''}">${im[i] ? `<img class="im" src="${im[i]}" alt="">` : '<div class="im"></div>'}
            ${c.label || c.figure ? `<h5>${esc(plain(c.figure ? c.figure + (c.label ? ' · ' + c.label : '') : c.label))}</h5>` : ''}${c.text ? `<p>${rich(c.text)}</p>` : ''}</div>`).join('')}</div></div></div>`;
      }
      case 'calles': {
        const rows = cards(s.cards).slice(0, 6);
        return dark(`<div class="cs-in fit">${T(s.title, [[24, 104], [44, 92], [999, 80]])}${S(s.text)}<div class="cs-streets">${rows.map(c => `<div class="cs-street"><h4>${rich(c.label)}</h4>${c.text ? `<p>${rich(c.text)}</p>` : ''}</div>`).join('')}</div></div>`);
      }
      case 'documento':
        return `<div class="cs cs-light"><div class="cs-logo">${logo(ctx, false)}</div><div class="cs-sheet" style="${bg(im[0])}"></div>
          <div class="cs-li fit" style="top:150px;bottom:600px;left:85px">${s.kicker ? `<div class="cs-doc-k">${esc(plain(s.kicker))}</div>` : ''}
          ${s.title ? `<div class="cs-doc-t" style="--ts:${tsize(s.title, [[12, 190], [20, 150], [999, 120]])}px">${esc(plain(s.title))}</div>` : ''}${s.text ? `<p class="cs-doc-s">${rich(s.text)}</p>` : ''}</div></div>`;
    }
    return '';
  }

  /* Ajusta el tamaño de letra (--k) hasta que todo cabe. El elemento tiene que estar en el documento. */
  function fit(el) {
    const boxes = [...el.querySelectorAll('.fit')];
    for (let k = 1; k >= 0.55; k -= 0.04) {
      el.style.setProperty('--k', k.toFixed(2));
      if (boxes.every(b => b.scrollHeight <= b.clientHeight + 4)) break;
    }
  }

  let fontsReady = null;
  const loadFonts = () => fontsReady = fontsReady || Promise.all(['400', '500', '600', '700', '800'].map(w => document.fonts.load(`${w} 40px AP`))
    .concat(['600', '700', '800'].map(w => document.fonts.load(`${w} 40px AB`)))).catch(() => null);
  async function mount(container, slide, ctx) {
    await loadFonts();
    container.innerHTML = html(slide, ctx);
    const el = container.firstElementChild;
    await Promise.all([...el.querySelectorAll('img')].map(i => i.decode().catch(() => null)));
    fit(el);
    return el;
  }

  /* ---------- exportar a PNG: SVG con foreignObject, fuentes e imágenes incrustadas ---------- */
  const cache = {};
  async function dataUrl(url) {
    if (!url || url.startsWith('data:')) return url;
    if (cache[url]) return cache[url];
    const blob = await (await fetch(url)).blob();
    return cache[url] = await new Promise(r => { const f = new FileReader(); f.onload = () => r(f.result); f.readAsDataURL(blob) });
  }
  async function cssText() {
    if (cache.css) return cache.css;
    let css = await (await fetch('/static/carousel.css')).text();
    const urls = [...new Set([...css.matchAll(/url\((\/static\/fonts\/[^)]+)\)/g)].map(m => m[1]))];
    for (const u of urls) css = css.split(`url(${u})`).join(`url(${await dataUrl(u)})`);
    return cache.css = css;
  }
  async function toPng(el) {
    const clone = el.cloneNode(true);
    for (const n of [clone, ...clone.querySelectorAll('[style]')]) {
      const st = n.getAttribute('style') || '';
      const m = [...st.matchAll(/url\('([^']+)'\)/g)];
      let out = st;
      for (const x of m) out = out.split(x[1]).join(await dataUrl(x[1]));
      if (m.length) n.setAttribute('style', out);
    }
    for (const img of clone.querySelectorAll('img')) img.setAttribute('src', await dataUrl(img.getAttribute('src')));
    const css = await cssText();
    const xml = new XMLSerializer().serializeToString(clone);
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1350"><foreignObject x="0" y="0" width="1080" height="1350"><div xmlns="http://www.w3.org/1999/xhtml"><style>${css}</style>${xml}</div></foreignObject></svg>`;
    const img = new Image();
    img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
    await img.decode();
    await new Promise(r => setTimeout(r, 60));
    const c = document.createElement('canvas'); c.width = 1080; c.height = 1350;
    c.getContext('2d').drawImage(img, 0, 0, 1080, 1350);
    return c.toDataURL('image/png');
  }
  return { LAYOUTS, FIELD_LABEL, html, mount, fit, toPng, lines, cards, plain };
})();
