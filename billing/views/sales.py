import re
import textwrap
from datetime import date
from urllib.parse import quote

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import inr
from ..models import PAYMENT_MODES, D, Party, Product, Sale, SaleItem, Setting, db
from ..services import BillError, cancel_sale, find_products, parse_decimal, parse_scan, save_sale
from .reports import date_range

bp = Blueprint("sales", __name__)


def _empty_draft():
    """Each cashier gets their own new bill, so two counters never share one."""
    sale = Sale.query.filter_by(status="draft", user_id=g.user.id).filter(~Sale.items.any()).order_by(
        Sale.id.desc()
    ).first()
    if sale is None:
        sale = Sale(taxed=Setting.get().gst_on_sales, user=g.user)
        db.session.add(sale)
        db.session.commit()
    return sale


def _draft(sale_id):
    sale = db.get_or_404(Sale, sale_id)
    if sale.status != "draft":
        flash(f"Bill {sale.number} is already saved.", "error")
        abort(redirect(url_for("sales.bill", sale_id=sale.id)))
    return sale


def _to_bill(sale, **params):
    return redirect(url_for("sales.bill", sale_id=sale.id, **params))


@bp.route("/")
def index():
    return redirect(url_for("sales.pos"))


@bp.route("/pos")
def pos():
    """Counter billing: always opens a fresh bill. Unsaved bills stay listed as held."""
    return _to_bill(_empty_draft())


@bp.route("/sales/<int:sale_id>")
def bill(sale_id):
    sale = db.get_or_404(Sale, sale_id)
    q = request.args.get("q", "").strip()
    return render_template(
        "sales/bill.html",
        sale=sale,
        customers=Party.query.filter_by(kind="customer").order_by(Party.name).all(),
        held=Sale.query.filter(Sale.status == "draft", Sale.id != sale.id, Sale.user_id == g.user.id,
                               Sale.items.any()).all(),
        matches=find_products(q) if q and sale.status == "draft" else [],
        q=q, last=request.args.get("last", type=int),
        add_qty=request.args.get("qty", "1"),
        modes=PAYMENT_MODES,
    )


@bp.route("/sales/<int:sale_id>/scan", methods=["POST"])
def scan(sale_id):
    sale = _draft(sale_id)
    qty, code = parse_scan(request.form.get("code", ""))
    matches = find_products(code)
    if len(matches) == 1:
        item = sale.add_product(matches[0], qty)
        sale.compute()
        db.session.commit()
        return _to_bill(sale, last=item.id)
    if not matches:
        flash(f"No product found for “{code}”." if code else "Scan a barcode or type a product name.", "error")
        return _to_bill(sale)
    return _to_bill(sale, q=code, qty=qty)


@bp.route("/sales/<int:sale_id>/add", methods=["POST"])
def add(sale_id):
    sale = _draft(sale_id)
    product = db.get_or_404(Product, request.form.get("product_id", type=int))
    qty = parse_decimal(request.form.get("qty", "1"), D(1))
    item = sale.add_product(product, qty if qty else D(1))
    sale.compute()
    db.session.commit()
    return _to_bill(sale, last=item.id)


def _apply_form(sale, form):
    for item in sale.items:
        qty = parse_decimal(form.get(f"qty_{item.id}"), D(item.qty))
        rate = parse_decimal(form.get(f"rate_{item.id}"), D(item.rate))
        disc = parse_decimal(form.get(f"disc_{item.id}"), D(item.disc_pct))
        if qty is None or rate is None or rate < 0 or disc is None or not 0 <= disc <= 100:
            raise BillError(f"Check the quantity, rate and discount of {item.name}.")
        item.qty, item.rate, item.disc_pct = qty, rate, disc

    party_id = form.get("party_id", type=int)
    party = db.session.get(Party, party_id) if party_id else None
    sale.party = party if party and party.kind == "customer" else None
    if form.get("payment_mode") in PAYMENT_MODES:
        sale.payment_mode = form["payment_mode"]
    discount = parse_decimal(form.get("discount", ""), D(0))
    paid = parse_decimal(form.get("paid", ""), D(0))
    if discount is None or discount < 0 or paid is None or paid < 0:
        raise BillError("Discount and paid amount must be positive numbers.")
    sale.discount, sale.paid = discount, paid


@bp.route("/sales/<int:sale_id>/update", methods=["POST"])
def update(sale_id):
    sale = _draft(sale_id)
    action = request.form.get("action", "update")
    try:
        _apply_form(sale, request.form)
    except BillError as e:
        db.session.rollback()
        flash(str(e), "error")
        return _to_bill(sale)
    if action.startswith("remove:"):
        item = db.session.get(SaleItem, int(action.split(":", 1)[1]))
        if item in sale.items:
            sale.items.remove(item)
    sale.compute()
    db.session.commit()

    if action not in ("save", "print"):
        return _to_bill(sale)
    try:
        sale.user = g.user
        save_sale(sale)
        db.session.commit()
    except BillError as e:
        db.session.rollback()
        flash(str(e), "error")
        return _to_bill(sale)
    if action == "print":
        return redirect(url_for("sales.receipt", sale_id=sale.id, autoprint=1))
    flash(f"Bill {sale.number} saved: ₹{sale.net}.", "success")
    return redirect(url_for("sales.pos"))


@bp.route("/sales/<int:sale_id>/customer", methods=["POST"])
def quick_customer(sale_id):
    """Add a customer from the billing screen (name + phone) and put them on the bill."""
    sale = _draft(sale_id)
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip() or None
    if not name:
        flash("Customer name is required.", "error")
        return _to_bill(sale)
    party = Party.query.filter_by(kind="customer", phone=phone).first() if phone else None
    if party is None:
        party = Party(kind="customer", name=name, phone=phone)
        db.session.add(party)
    sale.party = party
    db.session.commit()
    return _to_bill(sale)


@bp.route("/sales/<int:sale_id>/discard", methods=["POST"])
def discard(sale_id):
    db.session.delete(_draft(sale_id))
    db.session.commit()
    return redirect(url_for("sales.pos"))


@bp.route("/sales/<int:sale_id>/cancel", methods=["POST"])
def cancel(sale_id):
    sale = db.get_or_404(Sale, sale_id)
    try:
        cancel_sale(sale)
        db.session.commit()
        flash(f"Bill {sale.number} cancelled and stock returned.", "success")
    except BillError as e:
        flash(str(e), "error")
    return _to_bill(sale)


@bp.route("/sales/<int:sale_id>/receipt")
def receipt(sale_id):
    sale = db.get_or_404(Sale, sale_id)
    if sale.status == "draft":
        return _to_bill(sale)
    return render_template("sales/receipt.html", sale=sale)


@bp.route("/sales/<int:sale_id>/invoice")
def invoice(sale_id):
    """A4 GST tax invoice."""
    sale = db.get_or_404(Sale, sale_id)
    if sale.status == "draft":
        return _to_bill(sale)
    return render_template("sales/invoice.html", sale=sale)


def whatsapp_phone(phone):
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return "91" + digits if len(digits) == 10 else digits


WIDTH = 32  # characters per line of WhatsApp's monospace block on a phone


def _num(value):
    """Rates without needless decimals: 42.00 -> 42, 42.50 -> 42.5"""
    return f"{D(value).normalize():f}"


def bill_text(sale):
    """The bill as a WhatsApp message: *bold* headings, items in a ``` monospace ``` block so columns line up."""
    store = Setting.get()
    head = [f"*{store.store_name}*"]
    contact = " · ".join(filter(None, [store.address, f"Ph: {store.phone}" if store.phone else None]))
    if contact:
        head.append(contact)
    if store.gstin:
        head.append(f"GSTIN: {store.gstin}")
    if sale.status == "cancelled":
        head += ["", "*❌ THIS BILL IS CANCELLED*"]
    head += ["", f"🧾 *Bill No: {sale.number}*", f"📅 {sale.created_at:%d-%m-%Y, %I:%M %p}"]
    if sale.party:
        head.append(f"👤 {sale.customer_name}")

    rule = "-" * WIDTH
    table = ["Item".ljust(9) + "Qty x Rate".rjust(13) + "Amount".rjust(10), rule]
    for item in sale.items:
        table += textwrap.wrap(item.name, WIDTH) or [""]
        mrp = f"MRP {_num(item.mrp)}" if D(item.mrp) > D(item.rate) else ""
        qty_rate = f"{_num(item.qty)} x {_num(item.rate)}"
        if D(item.disc_pct):
            qty_rate += f" -{_num(item.disc_pct)}%"
        amount = inr(item.amount)
        row = mrp.ljust(9) + qty_rate.rjust(13) + amount.rjust(10)
        if len(row) > WIDTH:  # very long numbers: keep the amount at the right edge
            row = f"{mrp} {qty_rate} ".ljust(WIDTH - len(amount)) + amount
        table.append(row)
    table += [rule, f"Items: {len(sale.items)}   Qty: {_num(sale.total_qty)}"]

    def line(label, value):
        return f"{label}: ₹{inr(value)}"

    totals = []
    if D(sale.discount) or D(sale.round_off):
        totals.append(line("Subtotal", sale.gross))
        if D(sale.discount):
            totals.append(f"Discount: -₹{inr(sale.discount)}")
        if D(sale.round_off):
            totals.append(f"Round off: {'+' if sale.round_off > 0 else '-'}₹{inr(abs(sale.round_off))}")
    totals.append(f"*TOTAL: ₹{inr(sale.net)}*")
    if sale.taxed and D(sale.tax):
        totals.append(f"_(incl. GST ₹{inr(sale.tax)})_")
    if sale.payment_mode == "credit":
        totals.append(f"Paid: ₹{inr(sale.paid)}")
    else:
        totals.append(f"Paid by {sale.payment_mode.upper()} ✅")
    if sale.due > 0:
        totals.append(f"*Balance due: ₹{inr(sale.due)}*")
    if sale.savings > 0:
        totals += ["", f"🎉 *You saved ₹{inr(sale.savings)}!*"]

    footer = ["", f"_{store.bill_footer}_"] if store.bill_footer else []
    return "\n".join(head + ["", "```", *table, "```", ""] + totals + footer)


@bp.route("/sales/<int:sale_id>/whatsapp")
def whatsapp(sale_id):
    """Open WhatsApp (web or app) with the bill typed out, addressed to the customer's number."""
    sale = db.get_or_404(Sale, sale_id)
    if sale.status == "draft":
        return _to_bill(sale)
    phone = whatsapp_phone(request.args.get("phone") or (sale.party.phone if sale.party else ""))
    return redirect(f"https://wa.me/{phone}?text={quote(bill_text(sale))}")


@bp.route("/sales")
def sales_list():
    start, end, args = date_range(default_from=date.today())
    query = Sale.query.filter(Sale.status != "draft", Sale.created_at >= start, Sale.created_at < end)
    number = request.args.get("number", type=int)
    if number:
        query = Sale.query.filter_by(number=number)
    return render_template(
        "sales/list.html",
        sales=query.order_by(Sale.id.desc()).all(),
        held=Sale.query.filter(Sale.status == "draft", Sale.items.any()).all(),
        **args,
    )
