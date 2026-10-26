"""Pages anyone with the link can open: a customer's own bill (see invoice_links)."""

from flask import Blueprint, Response, abort, render_template, url_for
from markupsafe import escape

from ..invoice_links import valid
from ..models import Product, Sale, db

bp = Blueprint("public", __name__, url_prefix="/b")


def _sale(sale_id, sig):
    sale = db.session.get(Sale, sale_id)
    if sale is None or sale.status not in ("saved", "cancelled") or not valid(sale_id, sig):
        abort(404)
    return sale


@bp.route("/<int:sale_id>-<sig>")
def invoice(sale_id, sig):
    return render_template("sales/receipt.html", sale=_sale(sale_id, sig), public=True, sig=sig)


@bp.route("/<int:sale_id>-<sig>/a4")
def invoice_a4(sale_id, sig):
    return render_template("sales/invoice.html", sale=_sale(sale_id, sig), public=True, sig=sig)


# --- Search engines (robots.txt and sitemap.xml at the site root) ----------------------------

seo = Blueprint("seo", __name__)


@seo.route("/robots.txt")
def robots():
    from .shop import is_public

    if not is_public():
        return Response("User-agent: *\nDisallow: /\n", mimetype="text/plain")
    lines = ["User-agent: *", "Allow: /shop/", "Allow: /static/", "Disallow: /shop/cart", "Disallow: /shop/orders",
             "Disallow: /shop/login", "Disallow: /", f"Sitemap: {url_for('seo.sitemap', _external=True)}"]
    return Response("\n".join(lines) + "\n", mimetype="text/plain")


@seo.route("/sitemap.xml")
def sitemap():
    from .shop import is_public

    if not is_public():
        abort(404)
    urls = [url_for("shop.catalog", _external=True)]
    categories = db.session.query(Product.category).filter(Product.online.is_(True), Product.category.isnot(None)).distinct()
    urls += [url_for("shop.catalog", category=c, _external=True) for (c,) in categories if c]
    urls += [url_for("shop.product", product_id=p.id, slug=p.url_slug, _external=True)
             for p in Product.query.filter(Product.online.is_(True)).order_by(Product.id)]
    body = "".join(f"<url><loc>{escape(u)}</loc></url>" for u in urls)
    xml = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>\n'
    return Response(xml, mimetype="application/xml")
