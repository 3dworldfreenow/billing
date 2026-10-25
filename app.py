import os
import sys

if sys.version_info < (3, 11):
    sys.exit(f"This app needs Python 3.11 or newer; this is Python {sys.version.split()[0]}. See README: Requirements.")

from billing import create_app  # noqa: E402

app = create_app()

if __name__ == "__main__":
    from waitress import serve

    from billing.backup import start_scheduler

    start_scheduler(app)

    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "0.0.0.0")  # 127.0.0.1 = only reachable through Caddy/nginx
    print(f"Billing app running: open http://localhost:{port} (other devices: http://<this-pc-ip>:{port})")
    proxy = {}
    if os.environ.get("BEHIND_PROXY") == "1":  # trust Caddy/nginx on this server for https and the domain name
        proxy = {"trusted_proxy": "127.0.0.1", "trusted_proxy_headers": "x-forwarded-proto x-forwarded-host x-forwarded-for"}
    serve(app, host=host, port=port, threads=8, **proxy)
