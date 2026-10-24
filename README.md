# Billing

A small billing / invoicing app built with Python and Flask.

- **Customers** and **products** (each product can have a unique barcode)
- **Invoices** with line items, tax rate, status (draft → sent → paid / void), and a print view
- **Barcode scanning** on draft invoices:
  - **USB/Bluetooth scanners** work out of the box. They type the code and press Enter into the
    always-focused scan box. Scanning the same product again increases its quantity.
  - **Camera scanning** (phone or laptop) via the "Use camera" button, using
    [html5-qrcode](https://github.com/mebjas/html5-qrcode). Browsers allow camera access only on
    `https://` or `localhost`.
  - JSON lookup: `GET /api/products/barcode/<code>`

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/flask --app app run --debug
```

Open http://127.0.0.1:5000. Data is stored in SQLite at `instance/billing.db`.
Set `DATABASE_URL` to use another database and `SECRET_KEY` in production.

## Test

```bash
.venv/bin/pytest
```
