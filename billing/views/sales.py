import re
import textwrap
from datetime import date
from urllib.parse import quote, urlencode

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import inr
from ..models import PAYMENT_MODES, Batch, D, Party, Product, Promotion, Sale, SaleItem, Setting, db
from ..services import (BillError, apply_redeem, cancel_sale, create_return, find_products, log, parse_decimal,
                        resolve_scan, returnable_qty, save_sale)
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


def _recalc(sale):
    sale.compute(Promotion.running())


def _require(permission):
    if not g.user.can(permission):
        abort(403)


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
        offers=Promotion.running() if sale.status == "draft" else [],
        q=q, last=request.args.get("last", type=int),
        add_qty=request.args.get("qty", "1"),
        modes=PAYMENT_MODES,
    )


@bp.route("/sales/<int:sale_id>/scan", methods=["POST"])
def scan(sale_id):
    sale = _draft(sale_id)
    qty, matches, code = resolve_scan(request.form.get("code", ""))
    if len(matches) == 1:
        item = sale.add_product(matches[0], qty)
        _recalc(sale)
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
    _recalc(sale)
    db.session.commit()
    return _to_bill(sale, last=item.id)


def _apply_form(sale, form):
    limit = g.user.discount_limit
    for item in sale.items:
        qty = parse_decimal(form.get(f"qty_{item.id}"), D(item.qty))
        rate = parse_decimal(form.get(f"rate_{item.id}"), D(item.rate))
        disc = parse_decimal(form.get(f"disc_{item.id}"), D(item.disc_pct))
        if qty is None or rate is None or rate < 0 or disc is None or not 0 <= disc <= 100:
            raise BillError(f"Check the quantity, rate and discount of {item.name}.")
        if disc > limit and disc != D(item.disc_pct):
            raise BillError(f"Your discount limit is {limit.normalize():f}%.")
        if rate != D(item.rate) and not g.user.can("rates"):
            raise BillError("You are not allowed to change rates.")
        batch_id = form.get(f"batch_{item.id}", type=int)
        if batch_id and batch_id != item.batch_id:
            batch = db.session.get(Batch, batch_id)
            if batch and batch.product_id == item.product_id:
                item.use_batch(batch, sale)
                rate = D(item.rate)
        item.qty, item.rate, item.disc_pct = qty, rate, disc

    party_id = form.get("party_id", type=int)
    party = db.session.get(Party, party_id) if party_id else None
    party = party if party and party.kind == "customer" else None
    if party != sale.party:
        old_list = sale.price_list
        sale.party = party
        if sale.price_list != old_list:  # wholesale <-> retail customer: reprice the lines
            for item in sale.items:
                if item.product:
                    item.use_batch(item.batch, sale)
    if form.get("payment_mode") in PAYMENT_MODES:
        sale.payment_mode = form["payment_mode"]
    discount = parse_decimal(form.get("discount", ""), D(0))
    paid = parse_decimal(form.get("paid", ""), D(0))
    points = form.get("points", type=int, default=0)
    if discount is None or discount < 0 or paid is None or paid < 0:
        raise BillError("Discount and paid amount must be positive numbers.")
    if discount and discount != D(sale.discount) and D(sale.gross) > 0 and discount * 100 / D(sale.gross) > limit:
        raise BillError(f"Your discount limit is {limit.normalize():f}% of the bill.")
    sale.discount, sale.paid = discount, paid
    apply_redeem(sale, points if sale.party else 0)


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
    _recalc(sale)
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
        return after_save_print(sale)
    flash(f"Bill {sale.number} saved: ₹{sale.net}.", "success")
    return redirect(url_for("sales.pos"))


def after_save_print(sale):
    """Print straight to the thermal printer when one is set up, else open the browser receipt."""
    store = Setting.get()
    if store.printer_mode != "browser" and store.auto_print:
        from ..printing import PrinterError, print_sale

        try:
            print_sale(sale, open_drawer=sale.payment_mode == "cash")
            flash(f"{sale.title} saved and printed: ₹{sale.net}.", "success")
            return redirect(url_for("sales.pos"))
        except PrinterError as e:
            flash(f"Saved, but printing failed: {e}", "error")
    return redirect(url_for("sales.receipt", sale_id=sale.id, autoprint=1))


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
    for item in sale.items:  # a wholesale customer gets wholesale rates
        if item.product:
            item.use_batch(item.batch, sale)
    _recalc(sale)
    db.session.commit()
    return _to_bill(sale)


@bp.route("/sales/<int:sale_id>/discard", methods=["POST"])
def discard(sale_id):
    sale = _draft(sale_id)
    if sale.items:
        log("discard bill", f"Unsaved bill with {len(sale.items)} items, ₹{sale.net}")
    db.session.delete(sale)
    db.session.commit()
    return redirect(url_for("sales.pos"))


@bp.route("/sales/<int:sale_id>/cancel", methods=["POST"])
def cancel(sale_id):
    _require("cancel")
    sale = db.get_or_404(Sale, sale_id)
    try:
        cancel_sale(sale)
        db.session.commit()
        flash(f"{sale.title} cancelled and stock returned.", "success")
    except BillError as e:
        db.session.rollback()
        flash(str(e), "error")
    return _to_bill(sale)


@bp.route("/sales/<int:sale_id>/return", methods=["GET", "POST"])
def make_return(sale_id):
    """Take goods back from a saved bill and issue a credit note."""
    _require("cancel")
    sale = db.get_or_404(Sale, sale_id)
    lines = [(item, returnable_qty(item)) for item in sale.items if D(item.qty) > 0]
    if request.method == "POST":
        quantities = {}
        for item, _ in lines:
            qty = parse_decimal(request.form.get(f"qty_{item.id}", ""), D(0))
            if qty is None:
                flash(f"Check the quantity of {item.name}.", "error")
                return redirect(url_for("sales.make_return", sale_id=sale.id))
            quantities[item.id] = qty
        mode = request.form.get("mode", "cash")
        try:
            note = create_return(sale, quantities, mode if mode in PAYMENT_MODES else "cash", g.user)
            db.session.commit()
        except BillError as e:
            db.session.rollback()
            flash(str(e), "error")
            return redirect(url_for("sales.make_return", sale_id=sale.id))
        flash(f"{note.title} saved. Refund ₹{-note.net} ({note.payment_mode.upper()}).", "success")
        return _to_bill(note)
    return render_template("sales/return.html", sale=sale, lines=lines, modes=PAYMENT_MODES)


@bp.route("/sales/<int:sale_id>/print", methods=["POST"])
def print_direct(sale_id):
    from ..printing import PrinterError, print_sale

    sale = db.get_or_404(Sale, sale_id)
    try:
        print_sale(sale)
        flash(f"{sale.title} sent to the printer.", "success")
    except PrinterError as e:
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


def whatsapp_url(phone, text):
    """Open WhatsApp with a typed message. api.whatsapp.com directly: the wa.me redirect garbles some
    characters on WhatsApp Desktop/Web, and messages avoid emojis for the same reason."""
    params = {"text": text}
    if whatsapp_phone(phone):
        params["phone"] = whatsapp_phone(phone)
    return "https://api.whatsapp.com/send?" + urlencode(params, quote_via=quote)


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
        head += ["", f"*THIS {'CREDIT NOTE' if sale.is_return else 'BILL'} IS CANCELLED*"]
    number = f"Credit Note: {sale.doc_no}" if sale.is_return else f"Bill No: {sale.number}"
    head += ["", f"*{number}*", f"Date: {sale.created_at:%d-%m-%Y, %I:%M %p}"]
    if sale.is_return and sale.return_of:
        head.append(f"Against bill {sale.return_of.number}")
    if sale.party:
        head.append(f"Customer: {sale.customer_name}")

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
    if sale.total_discount or D(sale.round_off):
        totals.append(line("Subtotal", sale.gross))
        if D(sale.discount):
            totals.append(f"Discount: -₹{inr(sale.discount)}")
        if D(sale.promo_discount):
            totals.append(f"Offer ({sale.promo_note}): -₹{inr(sale.promo_discount)}")
        if D(sale.redeem_amount):
            totals.append(f"Points used ({sale.points_redeemed}): -₹{inr(sale.redeem_amount)}")
        if D(sale.round_off):
            totals.append(f"Round off: {'+' if sale.round_off > 0 else '-'}₹{inr(abs(sale.round_off))}")
    totals.append(f"*{'REFUND' if sale.is_return else 'TOTAL'}: ₹{inr(abs(D(sale.net)))}*")
    if sale.taxed and D(sale.tax):
        totals.append(f"_(incl. GST ₹{inr(sale.tax)})_")
    if sale.is_return:
        totals.append("Adjusted in your account" if sale.payment_mode == "credit"
                      else f"Refunded by {sale.payment_mode.upper()}")
    elif sale.payment_mode == "credit":
        totals.append(f"Paid: ₹{inr(sale.paid)}")
    else:
        totals.append(f"Paid by {sale.payment_mode.upper()}")
    if sale.due > 0:
        totals.append(f"*Balance due: ₹{inr(sale.due)}*")
    if sale.savings > 0 and not sale.is_return:
        totals += ["", f"*You saved ₹{inr(sale.savings)}!*"]
    if sale.points_earned and sale.party:
        totals.append(f"Points earned: {sale.points_earned} (balance {sale.party.points})")

    footer = ["", f"_{store.bill_footer}_"] if store.bill_footer else []
    return "\n".join(head + ["", "```", *table, "```", ""] + totals + footer)


@bp.route("/sales/<int:sale_id>/whatsapp")
def whatsapp(sale_id):
    """Open WhatsApp (web or app) with the bill typed out, addressed to the customer's number."""
    sale = db.get_or_404(Sale, sale_id)
    if sale.status == "draft":
        return _to_bill(sale)
    phone = request.args.get("phone") or (sale.party.phone if sale.party else "")
    return redirect(whatsapp_url(phone, bill_text(sale)))


@bp.route("/sales")
def sales_list():
    start, end, args = date_range(default_from=date.today())
    query = Sale.query.filter(Sale.status != "draft", Sale.created_at >= start, Sale.created_at < end)
    number = request.args.get("number", "").strip().upper()
    if number:
        query = Sale.query.filter_by(return_number=int(number[3:])) if number.startswith("CN-") and \
            number[3:].isdigit() else Sale.query.filter_by(number=int(number)) if number.isdigit() else query
    return render_template(
        "sales/list.html",
        sales=query.order_by(Sale.id.desc()).all(),
        held=Sale.query.filter(Sale.status == "draft", Sale.items.any()).all(),
        **args,
    )
