// Shared behaviour: keyboard shortcuts, scan box focus, auto-recalculate, camera scanning.
(function () {
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

  // Type-ahead dropdown for product/customer boxes: <input data-autocomplete="products|customers" data-pick=...>
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
        f.method = 'post'; f.action = input.dataset.addUrl;
        f.innerHTML = `<input name="product_id" value="${it.id}"><input name="qty" value="${esc(qty)}">`;
        document.body.appendChild(f); f.submit();
      } else if (mode === 'go') {
        location.href = input.dataset.goUrl.replace('__ID__', it.id);
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
  document.querySelectorAll('[data-autocomplete]').forEach(autocomplete);

  // Lists that filter while you type: <form data-live="#results"> replaces #results with the new page's.
  document.querySelectorAll('form[data-live]').forEach((form) => {
    let seq = 0;
    const refresh = debounce(async () => {
      const url = new URL(location.pathname, location.origin);
      url.search = new URLSearchParams(new FormData(form)).toString();
      const mine = ++seq;
      const res = await fetch(url);
      if (!res.ok || mine !== seq) return;
      const doc = new DOMParser().parseFromString(await res.text(), 'text/html');
      const fresh = doc.querySelector(form.dataset.live);
      if (fresh) document.querySelector(form.dataset.live).replaceWith(fresh);
      history.replaceState(null, '', url);
    }, 250);
    form.addEventListener('input', refresh);
    form.addEventListener('change', refresh);
  });

  const scan = document.querySelector('[data-scan]');

  // Keep the scan box focused so a USB/Bluetooth scanner always types into it,
  // unless the user clicked into another field or control.
  if (scan) {
    scan.focus();
    document.addEventListener('click', (e) => {
      if (!e.target.closest('input, select, textarea, button, a, label, video, #camera, .ac-list')) scan.focus();
    });
  }

  document.addEventListener('keydown', (e) => {
    const target = document.querySelector(`[data-key="${e.key}"]`);
    if (e.key === 'F2' && scan) { e.preventDefault(); scan.focus(); scan.select(); }
    else if (target) { e.preventDefault(); target.click(); }
  });

  // Lines form: recalculate when a quantity/rate/discount changes.
  document.querySelectorAll('form[data-autosubmit]').forEach((form) => {
    form.addEventListener('change', (e) => {
      if (e.target.matches('[data-recalc]')) form.requestSubmit(form.querySelector('[data-update]'));
    });
  });

  // Cash calculator: change to return.
  const received = document.getElementById('received');
  if (received) {
    const out = document.getElementById('change');
    const net = parseFloat(received.dataset.net || '0');
    received.addEventListener('input', () => {
      const change = (parseFloat(received.value) || 0) - net;
      out.textContent = change >= 0 ? '₹' + change.toFixed(2) : '—';
    });
  }

  // Camera scanning (phones/laptops). The library is loaded only on pages that need it.
  document.querySelectorAll('[data-camera]').forEach((btn) => {
    let scanner = null;
    const label = btn.innerHTML;
    const input = document.getElementById(btn.dataset.camera);
    const box = document.getElementById('camera');
    btn.addEventListener('click', async () => {
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
  });

  // Barcode fields in forms: the scanner's Enter moves to the next field instead of submitting.
  document.querySelectorAll('[data-barcode-field]').forEach((input) => {
    input.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      const fields = [...input.form.querySelectorAll('input, select, textarea')];
      (fields[fields.indexOf(input) + 1] || input).focus();
    });
  });

  const toggle = document.querySelector('.menu-toggle');
  if (toggle) toggle.addEventListener('click', () => document.querySelector('.sidebar').classList.toggle('open'));
})();
