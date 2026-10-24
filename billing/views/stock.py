from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..models import D, Product, StockAdjustment, db
from ..services import find_products, parse_decimal

bp = Blueprint("stock", __name__, url_prefix="/stock")
REASONS = {"count": "Physical count", "damaged": "Damaged", "expired": "Expired", "opening": "Opening stock"}


@bp.route("/")
def index():
    q = request.args.get("q", "").strip()
    picked = db.session.get(Product, request.args.get("product_id", type=int) or 0)
    matches = [picked] if picked else find_products(q) if q else []
    return render_template(
        "stock/index.html",
        q=q or (picked.name if picked else ""), matches=matches, reasons=REASONS,
        product=matches[0] if len(matches) == 1 else None,
        recent=StockAdjustment.query.order_by(StockAdjustment.id.desc()).limit(50).all(),
    )


@bp.route("/<int:product_id>/adjust", methods=["POST"])
def adjust(product_id):
    """Set a product's stock to what was counted (or write off damaged/expired units)."""
    product = db.get_or_404(Product, product_id)
    reason = request.form.get("reason", "count")
    reason = reason if reason in REASONS else "count"
    system = D(product.stock)
    if reason in ("damaged", "expired"):
        lost = parse_decimal(request.form.get("lost", ""))
        if lost is None or lost <= 0:
            flash("Enter how many units to write off.", "error")
            return redirect(url_for("stock.index", product_id=product.id))
        actual = system - lost
    else:
        actual = parse_decimal(request.form.get("actual", ""))
        if actual is None:
            flash("Enter the counted quantity.", "error")
            return redirect(url_for("stock.index", product_id=product.id))
    db.session.add(StockAdjustment(product=product, system_qty=system, actual_qty=actual, reason=reason,
                                   note=request.form.get("note", "").strip() or None))
    product.stock = actual
    db.session.commit()
    diff = actual - system
    word = "surplus" if diff > 0 else "shortage" if diff < 0 else "no change"
    flash(f"{product.name}: stock {system.normalize():f} → {actual.normalize():f} ({word}).", "success")
    return redirect(url_for("stock.index"))
