# Mart Billing

A modern billing and inventory app for marts and grocery stores, built with Python, Flask and SQLite
(Python's built-in database). It runs in any browser: the counter PC, a laptop or a phone.

## Features

**Billing (counter sales)**
- Barcode scanning with USB/Bluetooth scanners (they just type + Enter) or the phone/laptop **camera**
- Search by name when there is no barcode; `3*barcode` adds 3 at once; loose items by weight (1.250 kg)
- MRP, sale rate, line discount %, bill discount, automatic round off, "You saved ₹…" on the bill
- Cash (with change calculator), UPI (shows a **UPI QR code** for the exact amount), card, credit
- Hold a bill and start another; returns with a negative quantity; cancel a saved bill (stock goes back)
- **Print** an 80 mm thermal receipt (`F9` = save & print) or an **A4 GST tax invoice**
- **Send the bill on WhatsApp** to the customer's number (opens WhatsApp with the bill typed out)

**Purchases**: supplier bill entry by scanning, free quantity, discount, GST slab, new MRP/sale rate.
Saving adds stock and updates cost and prices. An unknown barcode can be created as a product on the spot.

**Products**: barcode, category, brand, HSN, unit, MRP, sale/purchase rate, GST slab, stock, low-stock alert.
**Import** from Excel/CSV (SwilERP exports work: `NameToDisplay`, `Curr.Qty`, `SaleRate`, …) and **export** to Excel/CSV.

**Stock**: physical count (shortage/surplus) and write-off of damaged/expired goods, with history.

**Customers & suppliers**: credit balances, receive/make payments, bill history.

**Reports** (date range, Excel/CSV export, print): dashboard, sales register, daily summary with cash/UPI/card
split, product-wise sales and profit, purchase register, GST summary (CGST/SGST by slab), stock valuation,
outstanding dues.

**GST**: tax slab master (GST 2.0 defaults: 0, 5, 18, 40, plus 3 and 0.25; add your own), prices
inclusive of GST, CGST + SGST split, and composition-scheme mode (Bill of Supply, no tax).

**Users**: login, admin + custom users with per-section access (e.g. a cashier who can only bill).

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

All data is in `instance/billing.db`. **Back it up** by copying that file (e.g. to Google Drive) regularly.

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
