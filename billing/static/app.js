// Shared behaviour: background saving (no page reloads), keyboard shortcuts, scan box focus,
// auto-recalculate, live search, camera scanning.
(function () {
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
  const $ = (sel, root = document) => root.querySelector(sel);

  // ---- Background navigation -------------------------------------------------------------
  // Forms in the staff app are sent with fetch and only the page's <main> (and the menu) is
  // swapped: no white flash, scroll and focus stay put. Anything else (a receipt, a download,
  // the login page) falls back to a normal page load. Add data-native to a form to opt out.
  const loadedScripts = new Set([...document.querySelectorAll('script[src]')].map((s) => s.src));
  const progress = {
    el: () => $('#progress'),
    start() { const el = this.el(); if (!el) return; el.style.opacity = '1'; el.style.width = '35%'; this.t = setTimeout(() => { el.style.width = '75%'; }, 300); },
    done() { const el = this.el(); if (!el) return; clearTimeout(this.t); el.style.width = '100%'; setTimeout(() => { el.style.opacity = '0'; el.style.width = '0'; }, 200); },
  };

  function toast(message) {
    const box = document.createElement('div');
    box.className = 'flash error toast';
    box.textContent = message;
    document.body.appendChild(box);
    setTimeout(() => box.remove(), 5000);
  }

  function runScripts(root) {
    root.querySelectorAll('script').forEach((old) => {
      if (old.src && loadedScripts.has(old.src)) return;
      const script = document.createElement('script');
      [...old.attributes].forEach((a) => script.setAttribute(a.name, a.value));
      script.textContent = old.textContent;
      if (script.src) loadedScripts.add(script.src);
      old.replaceWith(script);
    });
  }

  async function download(res) {
    const name = /filename="?([^";]+)"?/.exec(res.headers.get('content-disposition') || '');
    const url = URL.createObjectURL(await res.blob());
    const a = Object.assign(document.createElement('a'), { href: url, download: name ? name[1] : '' });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }

  async function visit(url, options = {}, push = true) {
    progress.start();
    try {
      const res = await fetch(url, { ...options, credentials: 'same-origin' });
      if (!(res.headers.get('content-type') || '').includes('text/html') || res.headers.get('content-disposition')) {
        await download(res);
        return;
      }
      const html = await res.text();
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const main = doc.querySelector('.app .main');
      if (!main) {  // another kind of page
        if (res.ok) { location.assign(res.url); return; }
        document.open(); document.write(html); document.close();  // e.g. "Forbidden": show it as it came
        return;
      }
      const samePage = new URL(res.url).pathname === location.pathname;
      const scroll = window.scrollY;
      document.title = doc.title;
      $('.app .main').replaceWith(main);
      const nav = doc.querySelector('.sidebar nav');
      if (nav) $('.sidebar nav').replaceWith(nav);
      $('.sidebar').classList.remove('open');
      if (push && res.url !== location.href) history.pushState({ app: true }, '', res.url);
      runScripts(main);
      init(main);
      window.scrollTo(0, samePage ? scroll : 0);
    } catch (err) {
      toast("Couldn't reach the app. Check the connection and try again.");
    } finally {
      progress.done();
    }
  }

  const busy = new WeakSet();
  document.addEventListener('submit', (e) => {
    const form = e.target;
    const action = new URL(form.getAttribute('action') || location.href, location.href);
    if (e.defaultPrevented || form.target || form.hasAttribute('data-native') || action.origin !== location.origin
        || !$('.app')) return;
    e.preventDefault();
    if (busy.has(form)) return;  // ignore double clicks while saving
    busy.add(form);
    let data;
    try { data = new FormData(form, e.submitter || undefined); } catch (err) {
      data = new FormData(form);
      if (e.submitter && e.submitter.name) data.append(e.submitter.name, e.submitter.value);
    }
    const method = (form.getAttribute('method') || 'get').toUpperCase();
    const request = method === 'GET'
      ? visit(`${action.origin}${action.pathname}?${new URLSearchParams(data)}`)
      : visit(action.href, { method, body: data });
    request.finally(() => { busy.delete(form); if (form.isConnected && form.dataset.temp !== undefined) form.remove(); });
  });

  window.addEventListener('popstate', () => { if ($('.app')) visit(location.href, {}, false); });

  // ---- Type-ahead dropdown ------------------------------------------------------------------
  // <input data-autocomplete="products|customers" data-pick=...>
  //   data-pick="add"        POST product_id (+ qty from a "3*" prefix) to data-add-url
  //   data-pick="go"         open data-go-url with __ID__ replaced
  //   data-pick="select:ID"  choose the option in <select id=ID> and fire its change event
  //   data-pick="fill-id"    show the name and put the id in the hidden input <id>_id
  // Enter without choosing a suggestion submits the form as typed, so USB scanners keep working.
  function autocomplete(input) {
    const kind = input.dataset.autocomplete;
    const wrap = document.createElement('div');
    wrap.className = 'ac-wrap';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    const list = document.createElement('div');
    list.className = 'ac-list';
    list.hidden = true;
    list.setAttribute('role', 'listbox');
    wrap.appendChild(list);
    input.setAttribute('autocomplete', 'off');
    let items = [], active = -1, seq = 0, qty = '1';

    const hide = () => { list.hidden = true; active = -1; };
    const render = () => {
      if (!items.length) { list.innerHTML = '<div class="ac-empty">No match</div>'; list.hidden = false; return; }
      list.innerHTML = items.map((it, i) => kind === 'products'
        ? `<div class="ac-item${i === active ? ' active' : ''}" data-i="${i}" role="option"><div><b>${esc(it.name)}</b><div class="small muted">${esc(it.barcode)}</div></div>
           <div class="right small"><b>₹${esc(it.rate)}</b> <span class="muted">MRP ${esc(it.mrp)}</span><div class="muted">Stock ${esc(it.stock)} ${esc(it.unit)}</div></div></div>`
        : `<div class="ac-item${i === active ? ' active' : ''}" data-i="${i}" role="option"><b>${esc(it.name)}</b><span class="muted">${esc(it.phone)}</span></div>`
      ).join('');
      list.hidden = false;
      const el = list.querySelector('.active'); if (el) el.scrollIntoView({ block: 'nearest' });
    };
    const load = debounce(async () => {
      let text = input.value.trim();
      const m = text.match(/^(-?\d+(?:\.\d+)?)[*xX](.+)$/);
      qty = m ? m[1] : '1';
      if (m) text = m[2].trim();
      // Skip long numbers: that's a barcode being scanned, the server handles it on Enter.
      if (text.length < 2 || /^\d{8,}$/.test(text)) { hide(); return; }
      const mine = ++seq;
      try {
        const res = await fetch(`/api/${kind}?q=${encodeURIComponent(text)}`);
        if (!res.ok || mine !== seq) return;
        items = await res.json(); active = -1; render();
      } catch (e) { /* offline or logged out: typing + Enter still works */ }
    }, 150);

    const pick = (it) => {
      hide();
      const mode = input.dataset.pick || '';
      if (mode === 'add') {
        const f = document.createElement('form');
        f.method = 'post'; f.action = input.dataset.addUrl; f.hidden = true; f.dataset.temp = '';
        f.innerHTML = `<input name="product_id" value="${it.id}"><input name="qty" value="${esc(qty)}">`;
        document.body.appendChild(f); f.requestSubmit();
      } else if (mode === 'go') {
        visit(input.dataset.goUrl.replace('__ID__', it.id));
      } else if (mode === 'fill-id') {
        input.value = it.name;
        document.getElementById(input.id + '_id').value = it.id;
      } else if (mode.startsWith('select:')) {
        const sel = document.getElementById(mode.slice(7));
        if (![...sel.options].some((o) => o.value == it.id)) sel.add(new Option(it.name, it.id));
        sel.value = it.id; input.value = '';
        sel.dispatchEvent(new Event('change'));
      }
    };

    input.addEventListener('input', load);
    input.addEventListener('keydown', (e) => {
      if (list.hidden || !items.length) return;
      if (e.key === 'ArrowDown') { e.preventDefault(); active = (active + 1) % items.length; render(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); active = (active - 1 + items.length) % items.length; render(); }
      else if (e.key === 'Escape') { hide(); }
      else if (e.key === 'Enter' && active >= 0) { e.preventDefault(); pick(items[active]); }
      else if (e.key === 'Enter') { hide(); }
    });
    list.addEventListener('mousedown', (e) => {
      const row = e.target.closest('.ac-item');
      if (row) { e.preventDefault(); pick(items[+row.dataset.i]); }
    });
    input.addEventListener('blur', () => setTimeout(hide, 150));
  }

  // Lists that filter while you type: <form data-live="#results"> replaces #results with the new page's.
  function liveSearch(form) {
    let seq = 0;
    const refresh = debounce(async () => {
      const url = new URL(location.pathname, location.origin);
      url.search = new URLSearchParams(new FormData(form)).toString();
      const mine = ++seq;
      const res = await fetch(url);
      if (!res.ok || mine !== seq) return;
      const doc = new DOMParser().parseFromString(await res.text(), 'text/html');
      const fresh = doc.querySelector(form.dataset.live);
      if (fresh) $(form.dataset.live).replaceWith(fresh);
      history.replaceState(history.state, '', url);
    }, 250);
    form.addEventListener('input', refresh);
    form.addEventListener('change', refresh);
  }

  // Camera scanning (phones/laptops). The library is loaded only on pages that need it.
  function camera(btn) {
    let scanner = null;
    const label = btn.innerHTML;
    btn.addEventListener('click', async () => {
      const input = document.getElementById(btn.dataset.camera);
      if (scanner) {
        await scanner.stop().catch(() => {}); scanner.clear(); scanner = null;
        btn.innerHTML = label; return;
      }
      if (!window.Html5Qrcode) { alert('Camera scanner is not available.'); return; }
      scanner = new Html5Qrcode('camera');
      btn.textContent = 'Stop camera';
      try {
        await scanner.start({ facingMode: 'environment' }, { fps: 10, qrbox: { width: 300, height: 140 } }, (code) => {
          scanner.stop().finally(() => {
            scanner.clear(); scanner = null; btn.innerHTML = label;
            input.value = code;
            if (input.form && input.dataset.submitOnScan !== undefined) input.form.requestSubmit();
            else input.dispatchEvent(new Event('change'));
          });
        });
      } catch (err) {
        scanner = null; btn.innerHTML = label;
        alert('Could not start the camera: ' + err + '\nCamera needs https:// or localhost.');
      }
    });
  }

  // ---- Per-page setup: runs on load and after every background update ------------------------
  function init(root) {
    root.querySelectorAll('[data-autocomplete]').forEach(autocomplete);
    root.querySelectorAll('form[data-live]').forEach(liveSearch);
    root.querySelectorAll('[data-camera]').forEach(camera);
    if (window.charts) window.charts.mount(root);
    // Barcode fields in forms: the scanner's Enter moves to the next field instead of submitting.
    root.querySelectorAll('[data-barcode-field]').forEach((input) => input.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      const fields = [...input.form.querySelectorAll('input, select, textarea')];
      (fields[fields.indexOf(input) + 1] || input).focus();
    }));
    // Cash calculator: change to return.
    const received = root.querySelector('#received');
    if (received) {
      const net = parseFloat(received.dataset.net || '0');
      received.addEventListener('input', () => {
        const change = (parseFloat(received.value) || 0) - net;
        $('#change').textContent = change >= 0 ? '₹' + change.toFixed(2) : '—';
      });
    }
    const focus = $('[data-scan]') || root.querySelector('[autofocus]');
    if (focus) focus.focus({ preventScroll: true });
  }

  // ---- Global handlers (registered once) -------------------------------------------------------
  // Keep the scan box focused so a USB/Bluetooth scanner always types into it,
  // unless the user clicked into another field or control.
  document.addEventListener('click', (e) => {
    if (e.target.closest('.menu-toggle')) { $('.sidebar').classList.toggle('open'); return; }
    const scan = $('[data-scan]');
    if (scan && !e.target.closest('input, select, textarea, button, a, label, video, #camera, .ac-list, details')) scan.focus();
  });

  document.addEventListener('keydown', (e) => {
    const scan = $('[data-scan]');
    const target = $(`[data-key="${e.key}"]`);
    if (e.key === 'F2' && scan) { e.preventDefault(); scan.focus(); scan.select(); }
    else if (target) { e.preventDefault(); target.click(); }
  });

  // Lines form: recalculate when a quantity/rate/discount changes.
  document.addEventListener('change', (e) => {
    const form = e.target.closest('form[data-autosubmit]');
    if (form && e.target.matches('[data-recalc]')) form.requestSubmit(form.querySelector('[data-update]'));
  });

  init(document);
})();
