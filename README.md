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
- **Invoice link** (online copy on https): the receipt, the bill screen and the thermal printout carry a QR code
  the customer scans to open and download their bill, and the WhatsApp message ends with the same link. Each link
  has a secret signature, so customers can't open each other's bills.

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

**Online shop (approved customers only)**: switch it on in Settings → Online shop, then approve customers on their
page (login = their mobile number + a password you set). They browse products with **photos** at `/shop`, fill a
cart and place an order with their address. Orders appear under **Online orders** (red count in the menu): call or
WhatsApp the customer, **Make bill** (stock changes when the bill is saved), then mark packed, out for delivery,
delivered; the customer sees each step. While the shop is closed (e.g. on the offline counter PC) only the admin can
preview it. Product photos are shrunk automatically and stored in `instance/uploads` (copy that folder along with
your backups).

**SEO mode** (Settings → Online shop, online copy only): anyone, including Google, can browse the shop, open product
pages and fill a cart; placing the order still needs an approved login (the cart moves into their account when they
log in). Each product gets its own page (`/shop/p/12-amul-butter-500g`) with page title, description, price and stock
for Google (schema.org data), plus `/sitemap.xml` and `/robots.txt`; visitors to your domain land in the shop and
staff log in at `/login`. Product form → **Online shop page & Google search**: description, Google title and
description (with a preview of the Google result) and web address.

**AI writing (Google Gemini)**: paste a free API key from aistudio.google.com in Settings → Online shop (admin), and
products get a **Write with AI** button that drafts the description and Google text from the name, brand and
category. Check the text, then save. **Load models** lists the models your key can use.

**Data safety**: automatic backups, **export all data** to one Excel file and download a database backup (then upload
them to Google Drive yourself).

### Keyboard shortcuts (billing screen)

| Key | Action |
| --- | --- |
| `F2` | Jump to the scan box / new bill |
| `F8` | Save bill |
| `F9` | Save & print |

## Requirements

Python **3.11 or newer** (3.12 recommended). Check with `python3 --version`.

## Run on a Linux server (VPS)

Older Linux servers ship Python 3.6, which is too old. Install a recent Python just for this app with
[uv](https://docs.astral.sh/uv/) (works on CentOS/RHEL/Rocky/Ubuntu, no system changes):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
cd billing                      # the unzipped app folder
uv venv --python 3.12 .venv
uv pip install --python .venv -r requirements.txt
.venv/bin/python app.py         # serves on port 5000
```

Open port 5000 in the firewall (`firewall-cmd --add-port=5000/tcp --permanent && firewall-cmd --reload` on
CentOS/Rocky, `ufw allow 5000` on Ubuntu) and browse to `http://<server-ip>:5000`. For use over the internet, put it
behind HTTPS on your own domain (below): logins should not travel over plain http, and phone cameras only scan on
https.

### Your own domain with HTTPS (e.g. billing.example.com)

1. **DNS:** at your domain provider add an **A record**: name `billing`, value = the server's IP address. Wait until
   `ping billing.example.com` shows that IP.
2. **Check ports 80 and 443 are free:** `ss -ltnp | grep -E ':(80|443) '`. If nginx or Apache already uses them,
   add the domain there instead (see "Already have nginx or Apache" below).
3. **Install [Caddy](https://caddyserver.com)** (one file; gets and renews the Let's Encrypt certificate by itself):

   ```bash
   curl -L -o /usr/local/bin/caddy "https://caddyserver.com/api/download?os=linux&arch=amd64"
   chmod +x /usr/local/bin/caddy
   mkdir -p /etc/caddy
   printf 'billing.example.com {\n    encode gzip\n    reverse_proxy 127.0.0.1:5010\n}\n' > /etc/caddy/Caddyfile
   firewall-cmd --add-service=http --add-service=https --permanent && firewall-cmd --reload   # Ubuntu: ufw allow 80,443/tcp
   nohup caddy run --config /etc/caddy/Caddyfile > /var/log/caddy.log 2>&1 &
   ```

4. **Run the app behind it**, reachable only through Caddy:

   ```bash
   BEHIND_PROXY=1 HOST=127.0.0.1 PORT=5010 nohup .venv/bin/python app.py > app.log 2>&1 &
   ```

   `BEHIND_PROXY=1` makes links (the online shop link, invoice links) use
   `https://billing.example.com` and keeps the login cookie https-only. `HOST=127.0.0.1` closes the plain-http port
   to the internet; you can also remove it from the firewall (`firewall-cmd --remove-port=5010/tcp --permanent`).

**Already have nginx or Apache** on ports 80/443 (`ss -ltnp | grep -E ':(80|443) '` shows which)? Add the domain
there instead of installing Caddy. On CentOS/RHEL first allow the web server to connect to the app (SELinux blocks it
otherwise and you get "502 Bad Gateway" / "503 Service Unavailable"):
`setsebool -P httpd_can_network_connect 1`.

*nginx:* `/etc/nginx/conf.d/billing.conf`

```nginx
server {
    listen 80;
    server_name billing.example.com;
    client_max_body_size 20m;
    location / {
        proxy_pass http://127.0.0.1:5010;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

then `nginx -t && systemctl reload nginx` and `certbot --nginx -d billing.example.com`.

*Apache:* `/etc/httpd/conf.d/billing.conf` (Ubuntu: `/etc/apache2/sites-available/billing.conf`, then
`a2enmod proxy proxy_http headers && a2ensite billing`)

```apache
<VirtualHost *:80>
    ServerName billing.example.com
    ProxyPreserveHost On
    RequestHeader set X-Forwarded-Proto "https" env=HTTPS
    ProxyPass / http://127.0.0.1:5010/
    ProxyPassReverse / http://127.0.0.1:5010/
</VirtualHost>
```

then `apachectl configtest && systemctl reload httpd` and `certbot --apache -d billing.example.com` (certbot copies
these lines into the https site it creates). Run the app as in step 4.

## Run on the shop PC

- **Windows:** install Python 3.12 from python.org (tick "Add Python to PATH"), then double-click `run_windows.bat`.
- **Linux/macOS:** `./run.sh`

Open http://localhost:5000. On first run you create the admin account, then fill in the store details
under **Settings**. Other devices on the shop Wi-Fi can open `http://<pc-ip>:5000`.
Browsers only allow the **camera** on `https://` or `localhost`, so camera scanning on phones needs the
online (https) setup below. USB scanners work everywhere.

All data is in `instance/billing.db`.

### Backups and export

**Settings → Backup & export** (admin only):

- **Export all data**: one Excel file, a sheet per table (products, bills, purchases, customers, payments, …).
- **Download database backup**: the whole database as one file. To keep a copy in Google Drive, open
  drive.google.com → New → File upload and pick the downloaded file.
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
