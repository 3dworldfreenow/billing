// Small offline charts for the dashboard: area (trend), donut (part of a whole) and
// horizontal bars. Each element with data-chart="area|donut|bars" and data-values='{json}'
// is drawn as SVG/HTML at its own width, and redrawn when the width changes.
(function () {
  const NS = 'http://www.w3.org/2000/svg';
  // Slot 0 is the gray used for context (last period, "Other"); 1-5 are the series colours.
  const COLORS = ['#a9aeb7', '#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4'];
  const INK = { grid: '#eef0f3', axis: '#d6d8de', label: '#8a909c' };

  const inr = (v) => '₹' + Math.round(v).toLocaleString('en-IN');
  function compact(v) {
    const a = Math.abs(v);
    const fmt = (n, s) => (Math.round(n * 10) / 10).toString().replace(/\.0$/, '') + s;
    if (a >= 1e7) return '₹' + fmt(v / 1e7, 'Cr');
    if (a >= 1e5) return '₹' + fmt(v / 1e5, 'L');
    if (a >= 1e3) return '₹' + fmt(v / 1e3, 'K');
    return '₹' + Math.round(v);
  }
  function niceMax(v) {
    if (v <= 0) return 100;
    const step = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * step >= v) return m * step;
    return 10 * step;
  }
  function el(name, attrs, parent) {
    const node = document.createElementNS(NS, name);
    for (const k in attrs) node.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(node);
    return node;
  }
  function h(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  // ---- Tooltip (one for the page) -------------------------------------------------------------
  let tip;
  function showTip(x, y, title, rows) {
    if (!tip || !tip.isConnected) { tip = h('div', 'viz-tip'); document.body.appendChild(tip); }
    tip.replaceChildren();
    if (title) tip.appendChild(h('div', 'viz-tip-title', title));
    rows.forEach((r) => {
      const row = h('div', 'viz-tip-row');
      const key = h('span', 'viz-key' + (r.dot ? ' dot' : ''));
      key.style.background = r.color;
      row.append(key, h('b', null, r.value), h('span', null, r.name || ''));
      tip.appendChild(row);
    });
    tip.hidden = false;
    const w = tip.offsetWidth, ht = tip.offsetHeight;
    let left = x + 14, top = y - ht - 10;
    if (left + w > window.innerWidth - 8) left = x - w - 14;
    if (top < 8) top = y + 16;
    tip.style.left = Math.max(8, left) + 'px';
    tip.style.top = top + 'px';
  }
  function hideTip() { if (tip) tip.hidden = true; }

  // ---- Area chart: this period (blue area + line) against the last one (gray line) -------------
  function area(box, data) {
    const W = box.clientWidth, H = 230, m = { l: 46, r: 14, t: 12, b: 26 };
    const pw = W - m.l - m.r, ph = H - m.t - m.b, n = data.labels.length;
    const all = data.series.flatMap((s) => s.values.filter((v) => v != null));
    const max = niceMax(Math.max(0, ...all));
    const x = (i) => m.l + (n === 1 ? pw / 2 : (i * pw) / (n - 1));
    const y = (v) => m.t + ph - (v / max) * ph;
    const svg = el('svg', { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'img', tabindex: 0,
      'aria-label': 'Sales trend. Use the arrow keys to read each point.' });

    for (let t = 0; t <= 4; t++) {
      const v = (max / 4) * t, yy = Math.round(y(v)) + 0.5;
      el('line', { x1: m.l, x2: W - m.r, y1: yy, y2: yy, stroke: t ? INK.grid : INK.axis }, svg);
      el('text', { x: m.l - 8, y: yy + 3.5, 'text-anchor': 'end', class: 'viz-axis' }, svg).textContent = compact(v);
    }
    const every = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(pw / 40))));
    data.labels.forEach((label, i) => {
      if (i % every) return;
      el('text', { x: x(i), y: H - 8, 'text-anchor': 'middle', class: 'viz-axis' }, svg).textContent = label;
    });

    const pathOf = (values) => {
      let d = '', open = false;
      values.forEach((v, i) => {
        if (v == null) { open = false; return; }
        d += (open ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1);
        open = true;
      });
      return d;
    };
    // Context first so the current period draws on top.
    [...data.series].reverse().forEach((s) => {
      const color = COLORS[s.color];
      const d = pathOf(s.values);
      if (!d) return;
      if (s.color) {
        const pts = s.values.map((v, i) => [v, i]).filter(([v]) => v != null);
        const fill = d + `L${x(pts[pts.length - 1][1]).toFixed(1)} ${y(0)}L${x(pts[0][1]).toFixed(1)} ${y(0)}Z`;
        el('path', { d: fill, fill: color, 'fill-opacity': 0.1 }, svg);
      }
      el('path', { d, fill: 'none', stroke: color, 'stroke-width': 2, 'stroke-linejoin': 'round',
        'stroke-linecap': 'round' }, svg);
    });
    const current = data.series[0];
    const lastIdx = current.values.reduce((a, v, i) => (v != null ? i : a), -1);
    if (lastIdx >= 0) {
      el('circle', { cx: x(lastIdx), cy: y(current.values[lastIdx]), r: 4, fill: COLORS[current.color],
        stroke: '#fff', 'stroke-width': 2 }, svg);
    }

    // Crosshair: snaps to the nearest point; the tooltip lists both periods.
    const guide = el('line', { y1: m.t, y2: m.t + ph, stroke: INK.axis, visibility: 'hidden' }, svg);
    const dots = data.series.map((s) => el('circle', { r: 4, fill: COLORS[s.color], stroke: '#fff',
      'stroke-width': 2, visibility: 'hidden' }, svg));
    const hit = el('rect', { x: m.l - 10, y: 0, width: pw + 20, height: H, fill: 'transparent' }, svg);
    let active = -1;
    function point(i, clientX, clientY) {
      active = i;
      guide.setAttribute('x1', x(i)); guide.setAttribute('x2', x(i));
      guide.setAttribute('visibility', 'visible');
      const rows = [];
      data.series.forEach((s, k) => {
        const v = s.values[i];
        dots[k].setAttribute('visibility', v == null ? 'hidden' : 'visible');
        if (v == null) return;
        dots[k].setAttribute('cx', x(i)); dots[k].setAttribute('cy', y(v));
        rows.push({ color: COLORS[s.color], value: inr(v), name: s.name });
      });
      if (clientX == null) {
        const r = svg.getBoundingClientRect();
        clientX = r.left + x(i); clientY = r.top + m.t + 20;
      }
      showTip(clientX, clientY, data.titles[i], rows);
    }
    function leave() {
      active = -1;
      guide.setAttribute('visibility', 'hidden');
      dots.forEach((d) => d.setAttribute('visibility', 'hidden'));
      hideTip();
    }
    hit.addEventListener('pointermove', (e) => {
      const r = svg.getBoundingClientRect();
      const i = Math.min(n - 1, Math.max(0, Math.round(((e.clientX - r.left - m.l) / pw) * (n - 1))));
      point(i, e.clientX, e.clientY);
    });
    hit.addEventListener('pointerleave', leave);
    svg.addEventListener('blur', leave);
    svg.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      point(Math.min(n - 1, Math.max(0, active < 0 ? lastIdx : active + (e.key === 'ArrowRight' ? 1 : -1))));
    });
    box.replaceChildren(svg);
  }

  // ---- Donut: a thin ring with 2px gaps, total in the middle, legend with values ---------------
  function donut(box, data) {
    const total = data.items.reduce((a, d) => a + d.value, 0);
    const wrap = h('div', 'viz-donut');
    if (total <= 0) {
      box.replaceChildren(h('p', 'muted small viz-empty', data.empty || 'Nothing in this period.'));
      return;
    }
    const S = 148, R = 64, ring = 14, C = 2 * Math.PI * R;
    const svg = el('svg', { width: S, height: S, viewBox: `0 0 ${S} ${S}`, role: 'img',
      'aria-label': data.title + ': ' + data.items.map((d) => `${d.name} ${inr(d.value)}`).join(', ') });
    const g = el('g', { transform: `rotate(-90 ${S / 2} ${S / 2})` }, svg);
    const shown = data.items.filter((d) => d.value > 0);
    const gap = shown.length > 1 ? 2 : 0;
    let offset = 0;
    data.items.forEach((d, i) => {
      const color = COLORS[d.other ? 0 : (i % 5) + 1];
      if (d.value > 0) {
        const len = (d.value / total) * C;
        const seg = el('circle', { cx: S / 2, cy: S / 2, r: R, fill: 'none', stroke: color, 'stroke-width': ring,
          'stroke-dasharray': `${Math.max(len - gap, 0.5)} ${C}`, 'stroke-dashoffset': -offset, class: 'viz-seg' }, g);
        const rows = [{ color, value: inr(d.value), name: `${d.name} · ${Math.round((d.value / total) * 100)}%`, dot: true }];
        seg.addEventListener('pointermove', (e) => showTip(e.clientX, e.clientY, null, rows));
        seg.addEventListener('pointerleave', hideTip);
        offset += len;
      }
    });
    el('text', { x: S / 2, y: S / 2 - 2, 'text-anchor': 'middle', class: 'viz-center' }, svg).textContent = compact(total);
    el('text', { x: S / 2, y: S / 2 + 15, 'text-anchor': 'middle', class: 'viz-axis' }, svg).textContent = data.caption || 'Total';
    const legend = h('div', 'viz-legend');
    data.items.forEach((d, i) => {
      const row = h('div', 'viz-legend-row');
      const key = h('span', 'viz-key dot');
      key.style.background = COLORS[d.other ? 0 : (i % 5) + 1];
      row.append(key, h('span', 'viz-legend-name', d.name), h('b', null, inr(d.value)),
        h('span', 'muted', Math.round((d.value / total) * 100) + '%'));
      legend.appendChild(row);
    });
    wrap.append(svg, legend);
    box.replaceChildren(wrap);
  }

  // ---- Horizontal bars: one hue, value at the tip ---------------------------------------------
  function bars(box, data) {
    if (!data.items.length) { box.replaceChildren(h('p', 'muted small viz-empty', data.empty || 'Nothing in this period.')); return; }
    const max = Math.max(...data.items.map((d) => d.value));
    const list = h('div', 'viz-bars');
    data.items.forEach((d) => {
      const row = h('div', 'viz-bar-row');
      const track = h('div', 'viz-bar-track');
      const bar = h('div', 'viz-bar');
      bar.style.width = Math.max(2, (d.value / max) * 100) + '%';
      track.appendChild(bar);
      row.append(h('div', 'viz-bar-name', d.name), track, h('b', 'viz-bar-value', compact(d.value)));
      const rows = [{ color: COLORS[1], value: inr(d.value), name: d.qty != null ? `${+d.qty.toFixed(3)} sold` : '' }];
      row.addEventListener('pointermove', (e) => showTip(e.clientX, e.clientY, d.name, rows));
      row.addEventListener('pointerleave', hideTip);
      list.appendChild(row);
    });
    box.replaceChildren(list);
  }

  const KINDS = { area, donut, bars };
  function draw(box) {
    try { KINDS[box.dataset.chart](box, JSON.parse(box.dataset.values)); } catch (e) { console.error(e); }
  }
  const sized = new WeakMap();
  const observer = 'ResizeObserver' in window ? new ResizeObserver((entries) => entries.forEach((en) => {
    const w = Math.round(en.contentRect.width);
    if (w && sized.get(en.target) !== w) { sized.set(en.target, w); draw(en.target); }
  })) : null;

  window.charts = {
    mount(root) {
      root.querySelectorAll('[data-chart]').forEach((box) => {
        draw(box);
        sized.set(box, Math.round(box.clientWidth));
        if (observer && box.dataset.chart === 'area') observer.observe(box);
      });
    },
  };
  window.addEventListener('scroll', hideTip, { passive: true });
})();
