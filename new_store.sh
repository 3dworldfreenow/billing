#!/usr/bin/env bash
# Create another store on this server, on its own (sub)domain, with its own data.
#
#   sudo ./new_store.sh shop2.example.com
#
# All stores share this folder's code (update once, restart all), but each store has its
# own database, photos, backups and logins in /var/lib/billing-stores/<domain>.
# Before running: point the domain's DNS (an A record) to this server's IP.
set -euo pipefail

DOMAIN="${1:-}"
CODE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_ROOT="${DATA_ROOT:-/var/lib/billing-stores}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
FIRST_PORT="${FIRST_PORT:-5011}"
HTTPD_DIR="${HTTPD_DIR:-/etc/httpd/conf.d}"
NGINX_DIR="${NGINX_DIR:-/etc/nginx/conf.d}"

say() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

# --- Checks -----------------------------------------------------------------------------------
[[ -n "$DOMAIN" ]] || die "Usage: sudo $0 shop2.example.com"
DOMAIN="$(echo "$DOMAIN" | tr 'A-Z' 'a-z')"
[[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]] || die "'$DOMAIN' is not a domain name."
[[ $EUID -eq 0 ]] || die "Run as root (sudo $0 $DOMAIN)."
[[ -f "$CODE_DIR/app.py" ]] || die "Put this script in the billing app folder (next to app.py)."

NAME="billing-${DOMAIN//./-}"
DATA_DIR="$DATA_ROOT/$DOMAIN"
UNIT="$SYSTEMD_DIR/$NAME.service"
[[ ! -e "$UNIT" ]] || die "$DOMAIN already exists (service $NAME). Nothing changed."
[[ ! -e "$DATA_DIR/billing.db" ]] || die "$DATA_DIR already has a store database. Nothing changed."

# Python: reuse the app's .venv, or create it with uv (works on old servers like CentOS 7).
PY="$CODE_DIR/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  say "Setting up Python for the app (first time only)"
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  (cd "$CODE_DIR" && uv venv --python 3.12 .venv && uv pip install --python .venv -r requirements.txt)
fi

# Web server already on this machine: Apache (CentOS: httpd, Ubuntu: apache2) or nginx.
if systemctl is-active --quiet httpd 2>/dev/null; then WEB=httpd
elif systemctl is-active --quiet apache2 2>/dev/null; then WEB=apache2
elif systemctl is-active --quiet nginx 2>/dev/null; then WEB=nginx
else die "No running Apache or nginx found. Install one (or see README: Caddy)."
fi

# --- Questions ------------------------------------------------------------------------------
say "New store on https://$DOMAIN"
read -rp "Store name (on bills and the shop): " STORE
[[ -n "$STORE" ]] || die "Store name is required."
read -rp "Admin's name [Owner]: " ADMIN_NAME; ADMIN_NAME="${ADMIN_NAME:-Owner}"
read -rp "Admin login username [admin]: " ADMIN_USER; ADMIN_USER="${ADMIN_USER:-admin}"
while true; do
  read -rsp "Admin password (6+ characters): " PASS1; echo
  read -rsp "Same password again: " PASS2; echo
  [[ "$PASS1" == "$PASS2" ]] && [[ ${#PASS1} -ge 6 ]] && break
  warn "Passwords don't match or are shorter than 6 characters. Try again."
done
read -rp "Email for the https certificate (optional, for expiry notices): " EMAIL

# --- Port: the next free one from $FIRST_PORT -------------------------------------------------
used_ports="$( { ss -ltn 2>/dev/null | awk 'NR>1{print $4}' | sed 's/.*://' || true
                 grep -ho 'PORT=[0-9]*' "$SYSTEMD_DIR"/billing*.service 2>/dev/null | cut -d= -f2 || true; } | sort -u)"
PORT=$FIRST_PORT
while grep -qx "$PORT" <<<"$used_ports"; do PORT=$((PORT + 1)); done

# --- Store data and admin account -------------------------------------------------------------
say "Creating the store's data folder $DATA_DIR"
mkdir -p "$DATA_DIR"
chmod 700 "$DATA_DIR"
(cd "$CODE_DIR" && INSTANCE_DIR="$DATA_DIR" STORE_ADMIN_PASSWORD="$PASS1" \
  "$PY" -m billing.setup_store --store "$STORE" --name "$ADMIN_NAME" --username "$ADMIN_USER")
unset PASS1 PASS2

# --- Service (starts on boot, restarts if it stops) -------------------------------------------
say "Starting the app as service $NAME on port $PORT"
cat > "$UNIT" <<EOF
[Unit]
Description=Billing app: $STORE ($DOMAIN)
After=network.target

[Service]
WorkingDirectory=$CODE_DIR
Environment=INSTANCE_DIR=$DATA_DIR BEHIND_PROXY=1 HOST=127.0.0.1 PORT=$PORT
ExecStart=$PY app.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now "$NAME"
for _ in $(seq 1 20); do
  curl -fsS -o /dev/null "http://127.0.0.1:$PORT/login" 2>/dev/null && break
  sleep 1
done
curl -fsS -o /dev/null "http://127.0.0.1:$PORT/login" || die "The app didn't start. See: journalctl -u $NAME -n 30"

# --- Web server: send $DOMAIN to the app ------------------------------------------------------
say "Adding $DOMAIN to $WEB"
if command -v getenforce >/dev/null && [[ "$(getenforce)" == "Enforcing" ]]; then
  setsebool -P httpd_can_network_connect 1   # let the web server reach the app
fi
case $WEB in
  httpd|apache2)
    if [[ $WEB == httpd ]]; then CONF="$HTTPD_DIR/$DOMAIN.conf"
    else CONF="/etc/apache2/sites-available/$DOMAIN.conf"; a2enmod -q proxy proxy_http headers; fi
    cat > "$CONF" <<EOF
<VirtualHost *:80>
    ServerName $DOMAIN
    ProxyPreserveHost On
    RequestHeader set X-Forwarded-Proto "https" env=HTTPS
    ProxyPass / http://127.0.0.1:$PORT/
    ProxyPassReverse / http://127.0.0.1:$PORT/
</VirtualHost>
EOF
    [[ $WEB == apache2 ]] && a2ensite -q "$DOMAIN"
    apachectl configtest
    systemctl reload "$WEB"
    PLUGIN=apache ;;
  nginx)
    CONF="$NGINX_DIR/$DOMAIN.conf"
    cat > "$CONF" <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    client_max_body_size 20m;
    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
    nginx -t
    systemctl reload nginx
    PLUGIN=nginx ;;
esac

# --- https certificate (Let's Encrypt) --------------------------------------------------------
HTTPS=no
server_ip="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)"
domain_ip="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1}')"
if ! command -v certbot >/dev/null; then
  warn "certbot is not installed, so the store is on http for now. Install it, then run:"
  echo "    certbot --$PLUGIN -d $DOMAIN --redirect"
elif [[ -n "$server_ip" && "$domain_ip" != "$server_ip" ]]; then
  warn "$DOMAIN points to '${domain_ip:-nothing}', not this server ($server_ip)."
  echo "    Add an A record for it, wait a few minutes, then run: certbot --$PLUGIN -d $DOMAIN --redirect"
else
  say "Getting the https certificate"
  if [[ -n "$EMAIL" ]]; then mail=(-m "$EMAIL"); else mail=(--register-unsafely-without-email); fi
  if certbot --"$PLUGIN" -d "$DOMAIN" --non-interactive --agree-tos --redirect "${mail[@]}"; then HTTPS=yes
  else warn "certbot failed (see above). The store works on http; run certbot again later."; fi
fi

# --- Done -------------------------------------------------------------------------------------
URL="http://$DOMAIN"; [[ $HTTPS == yes ]] && URL="https://$DOMAIN"
cat <<EOF

$(printf '\033[1;32m')Store ready: $STORE$(printf '\033[0m')
  Open:      $URL/login   (username: $ADMIN_USER)
  Data:      $DATA_DIR   (database, photos, backups)
  Service:   $NAME  on port $PORT
             systemctl restart $NAME   |   journalctl -u $NAME -n 30
  Web:       $CONF

Next: log in, fill Settings → Store (address, GSTIN, UPI) and add products.
After updating the app code, restart every store:  systemctl restart 'billing*'
EOF
