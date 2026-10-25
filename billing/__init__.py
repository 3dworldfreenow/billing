import os
import secrets
from datetime import date, timedelta
from urllib.parse import urlencode

import segno
from flask import Flask, g
from markupsafe import Markup

from .icons import icon
from .models import DEFAULT_TAX_SLABS, D, Setting, TaxSlab, db, money
from .schema import add_missing_columns


def inr(value):
    """Indian digit grouping: 1234567.5 -> 12,34,567.50"""
    value = money(value)
    sign = "-" if value < 0 else ""
    whole, frac = f"{abs(value):.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return sign + ",".join(groups + [tail]) + "." + frac


def qty(value):
    return f"{D(value).normalize():f}"


def upi_qr(amount, note=""):
    """Inline SVG QR code that opens any UPI app with the amount filled in."""
    store = Setting.get()
    if not store.upi_id or D(amount) <= 0:
        return None
    uri = "upi://pay?" + urlencode(
        {"pa": store.upi_id, "pn": store.store_name, "am": f"{money(amount)}", "cu": "INR", "tn": note}
    )
    return Markup(segno.make(uri, error="m").svg_inline(scale=4, border=2))


def _instance_secret(folder):
    """A random key kept next to the database, so logins survive restarts."""
    path = os.path.join(folder, "secret_key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
    with open(path) as f:
        return f.read().strip()


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev"),
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "DATABASE_URL", f"sqlite:///{os.path.join(app.instance_path, 'billing.db')}"
        ),
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,  # product import files
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SESSION_COOKIE_SAMESITE="Lax",  # other websites can't submit forms with your login
    )
    if config:
        app.config.update(config)
    if app.config.get("BEHIND_PROXY", os.environ.get("BEHIND_PROXY") == "1"):
        # Served through Caddy/nginx with HTTPS: trust its X-Forwarded-* headers so links,
        # the shop link and the Google Drive sign-in use https://your-domain.
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
        app.config["SESSION_COOKIE_SECURE"] = True

    os.makedirs(app.instance_path, exist_ok=True)
    if app.config["SECRET_KEY"] == "dev" and not app.testing:
        app.config["SECRET_KEY"] = _instance_secret(app.instance_path)
    db.init_app(app)

    from .views import (api, auth, cash, offers, orders, parties, products, purchases, reports, sales, settings,
                        shop, stock)

    for module in (api, auth, cash, offers, orders, shop, sales, purchases, products, parties, stock, reports, settings):
        app.register_blueprint(module.bp)
    app.before_request(auth.load_user)

    with app.app_context():
        db.create_all()
        added = add_missing_columns(db.engine, db.metadata)
        if added:
            app.logger.info("Database updated, new columns: %s", ", ".join(added))
        if Setting.get() is None:
            db.session.add(Setting(id=1))
            db.session.add_all(TaxSlab(rate=rate, name=name) for rate, name in DEFAULT_TAX_SLABS)
            db.session.commit()

    app.add_template_global(icon)  # a global, so imported macros can use it too
    app.add_template_filter(inr)
    app.add_template_filter(qty)

    @app.context_processor
    def globals_():
        return {"store": Setting.get(), "current_user": g.get("user"), "upi_qr": upi_qr, "today": date.today()}

    return app
