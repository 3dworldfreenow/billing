import math
import textwrap
from collections import defaultdict
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func

from ..models import PAYMENT_MODES, D, Party, Product, Purchase, PurchaseItem, Sale, SaleItem, Setting, TaxSlab, db
from ..services import BillError, cancel_purchase, find_products, parse_decimal, resolve_scan, save_purchase
from .reports import date_range
from .sales import whatsapp_url

bp = Blueprint("purchases", __name__, url_prefix="/purchases")
LINE_FIELDS = ("qty", "free_qty", "rate", "disc_pct", "gst_rate", "mrp", "sale_rate")


def _editable(purchase_id):
    purchase = db.get_or_404(Purchase, purchase_id)
    if not purchase.editable:
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
        purchases=Purchase.query.filter(Purchase.status.in_(("saved", "cancelled")), Purchase.created_at >= start,
                                        Purchase.created_at < end).order_by(Purchase.id.desc()).all(),
        drafts=Purchase.query.filter(Purchase.status == "draft", Purchase.items.any()).all(),
        orders=Purchase.query.filter_by(status="order").order_by(Purchase.id.desc()).all(),
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
        matches=find_products(q) if q and purchase.editable else [],
        q=q, last=request.args.get("last", type=int), add_qty=request.args.get("qty", "1"),
        slabs=TaxSlab.choices(), modes=PAYMENT_MODES,
    )


@bp.route("/<int:purchase_id>/scan", methods=["POST"])
def scan(purchase_id):
    purchase = _editable(purchase_id)
    qty, matches, code = resolve_scan(request.form.get("code", ""))
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
    purchase = _editable(purchase_id)
    product = db.get_or_404(Product, request.form.get("product_id", type=int))
    qty = parse_decimal(request.form.get("qty", "1"), D(1))
    item = purchase.add_product(product, qty if qty else D(1))
    purchase.compute()
    db.session.commit()
    return _to_bill(purchase, last=item.id)


def _parse_date(value, what):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        raise BillError(f"{what} is not a valid date.")


def _apply_form(purchase, form):
    for item in purchase.items:
        for field in LINE_FIELDS:
            value = parse_decimal(form.get(f"{field}_{item.id}"), D(getattr(item, field)))
            if value is None or (field != "qty" and value < 0) or (field == "disc_pct" and value > 100):
                raise BillError(f"Check the {field.replace('_', ' ')} of {item.product.name}.")
            setattr(item, field, value)
        if f"batch_no_{item.id}" in form:
            item.batch_no = form.get(f"batch_no_{item.id}", "").strip() or None
            item.expiry = _parse_date(form.get(f"expiry_{item.id}", ""), f"Expiry of {item.product.name}")
    party_id = form.get("party_id", type=int)
    party = db.session.get(Party, party_id) if party_id else None
    purchase.party = party if party and party.kind == "supplier" else None
    purchase.bill_no = form.get("bill_no", "").strip() or None
    purchase.bill_date = _parse_date(form.get("bill_date", ""), "Supplier bill date")
    if form.get("payment_mode") in PAYMENT_MODES:
        purchase.payment_mode = form["payment_mode"]
    paid = parse_decimal(form.get("paid", ""), D(0))
    if paid is None or paid < 0:
        raise BillError("Paid amount must be a positive number.")
    purchase.paid = paid


@bp.route("/<int:purchase_id>/update", methods=["POST"])
def update(purchase_id):
    purchase = _editable(purchase_id)
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
    if action == "receive":  # goods of an order arrived: continue as a purchase bill
        purchase.status = "draft"
        purchase.bill_date = purchase.bill_date or date.today()
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
    db.session.delete(_editable(purchase_id))
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
        db.session.rollback()
        flash(str(e), "error")
    return _to_bill(purchase)


# --- Purchase orders --------------------------------------------------------------


def _new_order(party=None):
    order = Purchase(status="order", party=party, user=g.user, order_number=Purchase.next_value(Purchase.order_number))
    db.session.add(order)
    return order


@bp.route("/orders/new")
def new_order():
    order = _new_order()
    db.session.commit()
    return _to_bill(order)


def reorder_suggestions(days=30, cover=15):
    """Products to buy: stock at or below the reorder point (the low-stock alert level, or a
    week of recent sales), with enough suggested to last `cover` days, grouped by last supplier."""
    since = datetime.now() - timedelta(days=days)
    sold = dict(
        db.session.query(SaleItem.product_id, func.sum(SaleItem.qty)).join(Sale)
        .filter(Sale.status == "saved", Sale.created_at >= since).group_by(SaleItem.product_id)
    )
    last_supplier = {}
    for product_id, party_id in (
        db.session.query(PurchaseItem.product_id, Purchase.party_id).join(Purchase)
        .filter(Purchase.status == "saved").order_by(Purchase.id.desc())
    ):
        last_supplier.setdefault(product_id, party_id)
    groups = defaultdict(list)
    for product in Product.query.order_by(Product.name):
        daily = D(sold.get(product.id)) / days
        stock, minimum = D(product.stock), D(product.min_stock)
        if minimum <= 0 and daily <= 0:
            continue
        if stock > max(minimum, daily * 7):
            continue
        target = max(minimum * 2, daily * cover)
        suggest = math.ceil(target - stock) if target > stock else 1
        groups[last_supplier.get(product.id)].append((product, daily, suggest))
    suppliers = {p.id: p for p in Party.query.filter(Party.id.in_([k for k in groups if k]))}
    ordered = sorted(groups.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))  # unknown supplier last
    return [(suppliers.get(pid), rows) for pid, rows in ordered]


@bp.route("/reorder", methods=["GET", "POST"])
def reorder():
    cover = max(request.args.get("cover", 15, type=int), 1)
    if request.method == "POST":
        party = db.session.get(Party, request.form.get("party_id", type=int) or 0)
        order = _new_order(party if party and party.kind == "supplier" else None)
        for key, value in request.form.items():
            if key.startswith("qty_"):
                qty = parse_decimal(value)
                product = db.session.get(Product, int(key[4:]))
                if product and qty and qty > 0 and request.form.get(f"pick_{product.id}"):
                    order.add_product(product, qty)
        if not order.items:
            db.session.rollback()
            flash("Tick at least one product.", "error")
            return redirect(url_for("purchases.reorder", cover=cover))
        order.compute()
        db.session.commit()
        flash(f"Purchase order {order.order_number} created.", "success")
        return _to_bill(order)
    return render_template("purchases/reorder.html", groups=reorder_suggestions(cover=cover), cover=cover)


@bp.route("/<int:purchase_id>/whatsapp")
def order_whatsapp(purchase_id):
    """Send a purchase order to the supplier on WhatsApp."""
    order = db.get_or_404(Purchase, purchase_id)
    store = Setting.get()
    lines = [f"*Purchase Order {order.order_number}*", f"From: *{store.store_name}*",
             f"Date: {date.today():%d-%m-%Y}", ""]
    for i, item in enumerate(order.items, start=1):
        name = textwrap.shorten(item.product.name, 40, placeholder="…")
        free = f" + {D(item.free_qty).normalize():f} free" if D(item.free_qty) else ""
        lines.append(f"{i}. {name} — *{D(item.qty).normalize():f} {item.product.unit}*{free}")
    signature = f"— {store.store_name}" + (f", {store.phone}" if store.phone else "")
    lines += ["", "Please confirm availability and rates.", signature]
    phone = request.args.get("phone") or (order.party.phone if order.party else "")
    return redirect(whatsapp_url(phone, "\n".join(lines)))
