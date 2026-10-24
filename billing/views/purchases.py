from datetime import date

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..models import PAYMENT_MODES, D, Party, Product, Purchase, PurchaseItem, TaxSlab, db
from ..services import BillError, cancel_purchase, find_products, parse_decimal, parse_scan, save_purchase
from .reports import date_range

bp = Blueprint("purchases", __name__, url_prefix="/purchases")
LINE_FIELDS = ("qty", "free_qty", "rate", "disc_pct", "gst_rate", "mrp", "sale_rate")


def _draft(purchase_id):
    purchase = db.get_or_404(Purchase, purchase_id)
    if purchase.status != "draft":
        flash(f"Purchase {purchase.number} is already saved.", "error")
        abort(redirect(url_for("purchases.bill", purchase_id=purchase.id)))
    return purchase


def _to_bill(purchase, **params):
    return redirect(url_for("purchases.bill", purchase_id=purchase.id, **params))


@bp.route("/new")
def new():
    purchase = Purchase.query.filter_by(status="draft", user_id=g.user.id).filter(~Purchase.items.any()).order_by(
        Purchase.id.desc()
    ).first()
    if purchase is None:
        purchase = Purchase(user=g.user)
        db.session.add(purchase)
        db.session.commit()
    return _to_bill(purchase)


@bp.route("/")
def index():
    start, end, args = date_range()
    return render_template(
        "purchases/list.html",
        purchases=Purchase.query.filter(Purchase.status != "draft", Purchase.created_at >= start,
                                        Purchase.created_at < end).order_by(Purchase.id.desc()).all(),
        drafts=Purchase.query.filter(Purchase.status == "draft", Purchase.items.any()).all(),
        **args,
    )


@bp.route("/<int:purchase_id>")
def bill(purchase_id):
    purchase = db.get_or_404(Purchase, purchase_id)
    q = request.args.get("q", "").strip()
    return render_template(
        "purchases/bill.html",
        purchase=purchase,
        suppliers=Party.query.filter_by(kind="supplier").order_by(Party.name).all(),
        matches=find_products(q) if q and purchase.status == "draft" else [],
        q=q, last=request.args.get("last", type=int), add_qty=request.args.get("qty", "1"),
        slabs=TaxSlab.choices(), modes=PAYMENT_MODES,
    )


@bp.route("/<int:purchase_id>/scan", methods=["POST"])
def scan(purchase_id):
    purchase = _draft(purchase_id)
    qty, code = parse_scan(request.form.get("code", ""))
    matches = find_products(code)
    if len(matches) == 1:
        item = purchase.add_product(matches[0], qty)
        purchase.compute()
        db.session.commit()
        return _to_bill(purchase, last=item.id)
    if not matches:
        # Unknown barcode on a purchase is usually a new product: offer to create it.
        flash(f"No product found for “{code}”.", "error")
        return _to_bill(purchase, unknown=code)
    return _to_bill(purchase, q=code, qty=qty)


@bp.route("/<int:purchase_id>/add", methods=["POST"])
def add(purchase_id):
    purchase = _draft(purchase_id)
    product = db.get_or_404(Product, request.form.get("product_id", type=int))
    qty = parse_decimal(request.form.get("qty", "1"), D(1))
    item = purchase.add_product(product, qty if qty else D(1))
    purchase.compute()
    db.session.commit()
    return _to_bill(purchase, last=item.id)


def _apply_form(purchase, form):
    for item in purchase.items:
        for field in LINE_FIELDS:
            value = parse_decimal(form.get(f"{field}_{item.id}"), D(getattr(item, field)))
            if value is None or (field != "qty" and value < 0) or (field == "disc_pct" and value > 100):
                raise BillError(f"Check the {field.replace('_', ' ')} of {item.product.name}.")
            setattr(item, field, value)
    party_id = form.get("party_id", type=int)
    party = db.session.get(Party, party_id) if party_id else None
    purchase.party = party if party and party.kind == "supplier" else None
    purchase.bill_no = form.get("bill_no", "").strip() or None
    try:
        purchase.bill_date = date.fromisoformat(form.get("bill_date", "")) if form.get("bill_date") else None
    except ValueError:
        raise BillError("Supplier bill date is not valid.")
    if form.get("payment_mode") in PAYMENT_MODES:
        purchase.payment_mode = form["payment_mode"]
    paid = parse_decimal(form.get("paid", ""), D(0))
    if paid is None or paid < 0:
        raise BillError("Paid amount must be a positive number.")
    purchase.paid = paid


@bp.route("/<int:purchase_id>/update", methods=["POST"])
def update(purchase_id):
    purchase = _draft(purchase_id)
    action = request.form.get("action", "update")
    try:
        _apply_form(purchase, request.form)
    except BillError as e:
        db.session.rollback()
        flash(str(e), "error")
        return _to_bill(purchase)
    if action.startswith("remove:"):
        item = db.session.get(PurchaseItem, int(action.split(":", 1)[1]))
        if item in purchase.items:
            purchase.items.remove(item)
    purchase.compute()
    db.session.commit()
    if action != "save":
        return _to_bill(purchase)
    try:
        purchase.user = g.user
        save_purchase(purchase)
        db.session.commit()
    except BillError as e:
        db.session.rollback()
        flash(str(e), "error")
        return _to_bill(purchase)
    flash(f"Purchase {purchase.number} saved. Stock and prices updated.", "success")
    return _to_bill(purchase)


@bp.route("/<int:purchase_id>/discard", methods=["POST"])
def discard(purchase_id):
    db.session.delete(_draft(purchase_id))
    db.session.commit()
    return redirect(url_for("purchases.index"))


@bp.route("/<int:purchase_id>/cancel", methods=["POST"])
def cancel(purchase_id):
    purchase = db.get_or_404(Purchase, purchase_id)
    try:
        cancel_purchase(purchase)
        db.session.commit()
        flash(f"Purchase {purchase.number} cancelled and stock reversed.", "success")
    except BillError as e:
        flash(str(e), "error")
    return _to_bill(purchase)
