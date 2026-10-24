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

Open http://127.0.0.1:5000. Without `DATABASE_URL`, data is stored in SQLite at
`instance/billing.db`. Set `SECRET_KEY` in production.

## Supabase

The app uses Supabase as its Postgres database.

1. In the Supabase dashboard, click **Connect** and copy the **Session pooler** connection string
   (the direct connection is IPv6-only). Avoid the transaction pooler (port 6543).
2. Set it as an environment variable. Never commit it:
   ```bash
   export DATABASE_URL="postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres"
   ```
3. Run the app as above.

The schema lives in `supabase/migrations/`. With the Supabase GitHub integration, migrations are
applied when they reach the production branch. The app also creates any missing tables at startup,
so it works against a fresh project too. All tables have Row Level Security enabled with no
policies, so the public Data API (anon key) can't read billing data. The app connects as the
database owner and is unaffected.

When you change `billing/models.py`, add a new migration file. Don't edit the existing one.

## Test

```bash
.venv/bin/pytest
# against Postgres (use a throwaway database; tables are dropped afterwards):
TEST_DATABASE_URL="postgresql://..." .venv/bin/pytest
```
