"""Links a customer can open to see and download their bill, without a login.

The link carries the bill's id and a short signature made with the app's secret key, so
nobody can guess other customers' bills by changing the number. Links are only offered
when the app is reached over https (the online copy on your domain): a link to the shop
PC would not open on the customer's phone."""

import hashlib
import hmac

import segno
from flask import current_app, request, url_for
from markupsafe import Markup


def signature(sale_id):
    key = current_app.config["SECRET_KEY"].encode()
    return hmac.new(key, f"invoice:{sale_id}".encode(), hashlib.sha256).hexdigest()[:12]


def valid(sale_id, sig):
    return hmac.compare_digest(signature(sale_id), sig or "")


def invoice_url(sale, force=False):
    """The customer's link for this bill, or None when it couldn't be opened from outside."""
    if sale is None or sale.status not in ("saved", "cancelled"):
        return None
    if not (force or request.scheme == "https" or current_app.config.get("INVOICE_LINKS")):
        return None
    return url_for("public.invoice", sale_id=sale.id, sig=signature(sale.id), _external=True)


def qr_svg(data, scale=3):
    return Markup(segno.make(data, error="m").svg_inline(scale=scale, border=2)) if data else None
