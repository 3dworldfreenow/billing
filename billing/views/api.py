"""JSON search used by the live search boxes."""

from flask import Blueprint, abort, g, jsonify, request

from ..models import D, Party
from ..services import product_query

bp = Blueprint("api", __name__, url_prefix="/api")


def _require_any(*permissions):
    if not any(g.user.can(p) for p in permissions):
        abort(403)


@bp.route("/products")
def products():
    _require_any("sales", "purchases", "products", "stock", "reports")
    q = request.args.get("q", "").strip()
    found = product_query(q).limit(12).all() if q else []
    return jsonify([
        {"id": p.id, "name": p.name, "barcode": p.barcode or "", "mrp": f"{p.mrp:.2f}",
         "rate": f"{p.sale_rate:.2f}", "stock": f"{D(p.stock).normalize():f}", "unit": p.unit}
        for p in found
    ])


@bp.route("/customers")
def customers():
    _require_any("sales", "parties")
    q = request.args.get("q", "").strip()
    query = Party.query.filter_by(kind="customer")
    for word in q.split():
        query = query.filter(Party.name.ilike(f"%{word}%") | Party.phone.ilike(f"%{word}%"))
    found = query.order_by(Party.name).limit(12).all() if q else []
    return jsonify([{"id": p.id, "name": p.name, "phone": p.phone or ""} for p in found])
