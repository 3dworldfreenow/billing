// Shared behaviour: keyboard shortcuts, scan box focus, auto-recalculate, camera scanning.
(function () {
  const scan = document.querySelector('[data-scan]');

  // Keep the scan box focused so a USB/Bluetooth scanner always types into it,
  // unless the user clicked into another field or control.
  if (scan) {
    scan.focus();
    document.addEventListener('click', (e) => {
      if (!e.target.closest('input, select, textarea, button, a, label, video, #camera')) scan.focus();
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
    const input = document.getElementById(btn.dataset.camera);
    const box = document.getElementById('camera');
    btn.addEventListener('click', async () => {
      if (scanner) {
        await scanner.stop().catch(() => {}); scanner.clear(); scanner = null;
        btn.textContent = '📷 Camera'; return;
      }
      if (!window.Html5Qrcode) { alert('Camera scanner is not available.'); return; }
      scanner = new Html5Qrcode('camera');
      btn.textContent = 'Stop camera';
      try {
        await scanner.start({ facingMode: 'environment' }, { fps: 10, qrbox: { width: 300, height: 140 } }, (code) => {
          scanner.stop().finally(() => {
            scanner.clear(); scanner = null; btn.textContent = '📷 Camera';
            input.value = code;
            if (input.form && input.dataset.submitOnScan !== undefined) input.form.requestSubmit();
            else input.dispatchEvent(new Event('change'));
          });
        });
      } catch (err) {
        scanner = null; btn.textContent = '📷 Camera';
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
