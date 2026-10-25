"""Pages anyone with the link can open: a customer's own bill (see invoice_links)."""

from flask import Blueprint, abort, render_template

from ..invoice_links import valid
from ..models import Sale, db

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
