# Mart Billing

A modern billing and inventory app for marts and grocery stores, built with Python, Flask and SQLite
(Python's built-in database). It runs in any browser: the counter PC, a laptop or a phone.

## Features

**Billing (counter sales)**
- Barcode scanning with USB/Bluetooth scanners or the phone/laptop **camera**; live product search while typing
- **Weighing-scale barcodes** (prefix + PLU + weight or price) for loose items
- MRP, sale rate, line discount %, bill discount, automatic round off, "You saved ₹…"
- **Batches & multiple MRP**: sells the batch expiring first; the cashier can pick another MRP on the line
- **Offers** applied automatically: % off a product/category, buy X get Y free, bill-value offers, dates and happy hours
- **Loyalty points**: earn per ₹100, redeem as a discount on the bill
- **Wholesale customers** get wholesale rates
- Cash (change calculator), UPI (QR code for the exact amount), card, credit; hold a bill and start another
- **Returns** from a saved bill make a **credit note** (CN-1, CN-2…), refunded in cash/UPI/card or to the customer's account
- Print an 80 mm receipt (browser, or **straight to a thermal printer** with cash drawer), an A4 GST invoice,
  or **send on WhatsApp**

**Cash register**: opening cash, cash in/out, expenses, end-of-day close per cashier (expected vs counted cash).

**Purchases**: supplier bills with free qty, discount, GST, MRP/sale rate, **batch no. and expiry**; saving adds stock
and updates costs. **Reorder list** from low stock and sales speed, **purchase orders** (print or WhatsApp to the
supplier) that turn into purchases when goods arrive.

**Products**: barcode (or a generated in-store EAN-13), scale PLU, category, brand, HSN, MRP, sale/wholesale/purchase
rate, GST slab, stock, low-stock alert. **Price labels** (A4 sheets or label roll). **Import/export** Excel/CSV
(SwilERP exports work).

**Stock**: physical count (shortage/surplus) and write-off of damaged/expired goods, per batch, with history.

**Customers & suppliers**: opening balances, credit balances, payments, loyalty points, **import from Excel**,
**WhatsApp payment reminders**.

**Reports** (date range, Excel/CSV, print): dashboard, sales register, daily summary, product-wise sales, net profit
after expenses, expenses, category margin, fast/slow/dead stock, expiry, purchases, stock valuation, outstanding,
GST summary and a **GSTR-1 / GSTR-3B workbook** for your CA.

**GST**: tax slab master (GST 2.0: 0, 5, 18, 40, plus 3 and 0.25; add your own), GST-inclusive prices, CGST + SGST,
composition-scheme mode (Bill of Supply).

**Users & control**: login, admin + custom users with per-section access, per-user discount limit, permissions to
change rates or cancel bills, and an **audit log** of cancellations, returns, price/stock changes and more.

**Data safety**: automatic backups, **export all data** to one Excel file, and **direct upload to Google Drive**.

### Keyboard shortcuts (billing screen)

| Key | Action |
| --- | --- |
| `F2` | Jump to the scan box / new bill |
| `F8` | Save bill |
| `F9` | Save & print |

## Run on the shop PC

- **Windows:** install Python 3 from python.org, then double-click `run_windows.bat`.
- **Linux/macOS:** `./run.sh`

Open http://localhost:5000. On first run you create the admin account, then fill in the store details
under **Settings**. Other devices on the shop Wi-Fi can open `http://<pc-ip>:5000`.
Browsers only allow the **camera** on `https://` or `localhost`, so camera scanning on phones needs the
online (https) setup below. USB scanners work everywhere.

All data is in `instance/billing.db`.

### Backups and Google Drive

**Settings → Backup & Google Drive** (admin only):

- **Export all data**: one Excel file, a sheet per table (products, bills, purchases, customers, payments, …).
- **Google Drive (direct upload)**: a one-time setup on that page (create a free Google OAuth client, about
  10 minutes), then **Upload to Google Drive now** sends a database backup plus the Excel export to a
  "<store> Backups" folder. Tick the box to also upload with every automatic backup.
- **Automatic backups** to a folder on the PC every few hours (default 6, keeping 30). Pointing that folder at a
  Google Drive for desktop folder (e.g. `G:\My Drive\P MART Backups`) also syncs them to Drive.
- To restore: close the app, copy a backup into `instance`, rename it to `billing.db`, and start the app again.

### Printer and scale

**Settings → Scale & printer**: thermal printers on the network (`IP:9100`) or on Windows (install `pywin32` once:
`.venv\Scripts\pip install pywin32`), paper width, automatic printing and cash-drawer opening; weighing-scale
barcode format (prefix, item-code length, weight or price). Set each loose product's **Scale PLU** to match the scale.

Updating the app is safe: new versions add any new database columns automatically.

## Run it from GitHub (Codespaces)

On the repository page click **Code → Codespaces → Create codespace**. GitHub builds it, starts the app
and opens it in a new tab with an `https://…app.github.dev` link (camera scanning works there).
Good for demos and testing. A codespace stops after 30 minutes without use and the free plan includes
about 60 hours a month, so for daily billing use the shop PC or Render.

## Put it online (Render)

GitHub stores the code. It can't run a Python app, so use a host that deploys from GitHub:

1. Sign in at [render.com](https://render.com) with GitHub.
2. **New → Blueprint**, pick this repository. `render.yaml` sets everything up, including a disk so
   the database survives deploys (paid "starter" instance).
3. Every push to the main branch redeploys automatically.

## Develop

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/flask --app app run --debug
.venv/bin/pytest
```
