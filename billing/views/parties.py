from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .. import inr
from ..exporting import cell_text, map_columns, read_rows
from ..models import PAYMENT_MODES, Party, PartyPayment, Setting, db
from ..services import log, parse_decimal
from .sales import whatsapp_url

bp = Blueprint("parties", __name__)
LABELS = {"customer": "Customers", "supplier": "Suppliers"}
ALIASES = {
    "name": ["name", "customername", "suppliername", "partyname", "accountname", "ledgername"],
    "phone": ["phone", "mobile", "mobileno", "phoneno", "contact", "contactno", "whatsapp"],
    "address": ["address", "address1", "city"],
    "gstin": ["gstin", "gstno", "gst", "gstnumber"],
    "opening_balance": ["openingbalance", "opening", "balance", "outstanding", "closingbalance", "due"],
}


def _list(kind):
    q = request.args.get("q", "").strip()
    query = Party.query.filter_by(kind=kind)
    for word in q.split():
        query = query.filter(Party.name.ilike(f"%{word}%") | Party.phone.ilike(f"%{word}%"))
    return render_template("parties/list.html", parties=query.order_by(Party.name).all(), kind=kind,
                           title=LABELS[kind], q=q)


@bp.route("/customers")
def customers():
    return _list("customer")


@bp.route("/suppliers")
def suppliers():
    return _list("supplier")


@bp.route("/customers/new", methods=["GET", "POST"], defaults={"kind": "customer"})
@bp.route("/suppliers/new", methods=["GET", "POST"], defaults={"kind": "supplier"})
@bp.route("/parties/<int:party_id>/edit", methods=["GET", "POST"])
def edit(kind=None, party_id=None):
    party = db.get_or_404(Party, party_id) if party_id else Party(kind=kind)
    if request.method == "POST":
        for field in ("name", "phone", "address", "gstin"):
            setattr(party, field, request.form.get(field, "").strip() or None)
        party.price_list = "wholesale" if request.form.get("wholesale") == "1" else "retail"
        opening = parse_decimal(request.form.get("opening_balance", ""), 0)
        if not party.name:
            flash("Name is required.", "error")
        elif opening is None:
            flash("Opening balance must be a number.", "error")
        else:
            if party.id and opening != party.opening_balance:
                log("opening balance", f"{party.name}: ₹{party.opening_balance} → ₹{opening}")
            party.opening_balance = opening
            db.session.add(party)
            db.session.commit()
            flash(f"{party.name} saved.", "success")
            return redirect(url_for("parties.detail", party_id=party.id))
    return render_template("parties/form.html", party=party)


@bp.route("/parties/<int:party_id>")
def detail(party_id):
    party = db.get_or_404(Party, party_id)
    return render_template("parties/detail.html", party=party, documents=party.documents.limit(100).all(),
                           modes=[m for m in PAYMENT_MODES if m != "credit"])


@bp.route("/parties/<int:party_id>/payment", methods=["POST"])
def payment(party_id):
    party = db.get_or_404(Party, party_id)
    amount = parse_decimal(request.form.get("amount", ""))
    mode = request.form.get("mode", "cash")
    if amount is None or amount <= 0:
        flash("Enter an amount greater than zero.", "error")
    else:
        db.session.add(PartyPayment(party=party, amount=amount, mode=mode if mode in PAYMENT_MODES else "cash",
                                    note=request.form.get("note", "").strip() or None, user_id=g.user.id))
        db.session.commit()
        word = "Received" if party.kind == "customer" else "Paid"
        flash(f"{word} ₹{amount} {'from' if party.kind == 'customer' else 'to'} {party.name}.", "success")
    return redirect(url_for("parties.detail", party_id=party.id))


@bp.route("/parties/<int:party_id>/shop", methods=["POST"])
def shop_access(party_id):
    """Approve a customer for the online shop (login = their mobile number) and set their password."""
    party = db.get_or_404(Party, party_id)
    password = request.form.get("password", "")
    allow = request.form.get("shop_access") == "1"
    if party.kind != "customer":
        flash("Only customers can use the online shop.", "error")
    elif allow and not party.phone:
        flash("Add the customer's mobile number first: it is their login.", "error")
    elif allow and not party.shop_password_hash and len(password) < 6:
        flash("Set a password of at least 6 characters.", "error")
    elif password and len(password) < 6:
        flash("Password must be at least 6 characters.", "error")
    else:
        party.shop_access = allow
        if password:
            party.set_shop_password(password)
        log("shop access", f"{party.name}: {'allowed' if allow else 'blocked'}{', new password' if password else ''}")
        db.session.commit()
        flash(f"{party.name} can {'now' if allow else 'no longer'} order online.", "success")
    return redirect(url_for("parties.detail", party_id=party.id))


def reminder_text(party):
    store = Setting.get()
    lines = [f"Namaste {party.name},", "",
             f"This is a friendly reminder from *{store.store_name}*.",
             f"Your pending balance is *₹{inr(party.balance)}*."]
    if store.upi_id:
        lines += ["", f"You can pay by UPI to: *{store.upi_id}*"]
    lines += ["", "Please ignore if already paid. Thank you!", f"— {store.store_name}"
              + (f", {store.phone}" if store.phone else "")]
    return "\n".join(lines)


@bp.route("/parties/<int:party_id>/remind")
def remind(party_id):
    """Open WhatsApp with a polite payment reminder for a customer's balance."""
    party = db.get_or_404(Party, party_id)
    return redirect(whatsapp_url(party.phone, reminder_text(party)))


@bp.route("/customers/import", methods=["GET", "POST"], defaults={"kind": "customer"})
@bp.route("/suppliers/import", methods=["GET", "POST"], defaults={"kind": "supplier"})
def import_(kind):
    back = redirect(url_for("parties.import_", kind=kind))
    if request.method == "GET":
        return render_template("parties/import.html", kind=kind, aliases=ALIASES)
    upload = request.files.get("file")
    try:
        rows = list(read_rows(upload)) if upload and upload.filename else []
    except Exception as e:  # unreadable/corrupt spreadsheet
        flash(f"Couldn't read the file: {e}", "error")
        return back
    mapping = map_columns(rows, ALIASES)
    if "name" not in mapping:
        flash("Choose a file with a 'Name' column.", "error")
        return back
    existing = Party.query.filter_by(kind=kind).all()
    by_phone = {p.phone: p for p in existing if p.phone}
    by_name = {p.name.lower(): p for p in existing}
    created = updated = 0
    problems = []
    for line, row in enumerate(rows, start=2):
        values = {field: cell_text(row.get(source)) for field, source in mapping.items()}
        name = values.get("name")
        if not name:
            continue
        party = by_phone.get(values.get("phone")) or by_name.get(name.lower())
        if party is None:
            party = Party(kind=kind, name=name)
            db.session.add(party)
            created += 1
        else:
            updated += 1
        for field in ("phone", "address", "gstin"):
            if values.get(field):
                setattr(party, field, values[field])
        if values.get("opening_balance"):
            text = values["opening_balance"].replace(",", "").replace("₹", "").strip()
            # Tally/SwilERP style "1200 Dr" (owes us) / "300 Cr" (we owe them)
            sign = -1 if text.lower().endswith("cr") and kind == "customer" or \
                text.lower().endswith("dr") and kind == "supplier" else 1
            amount = parse_decimal(text.rstrip("DdCcRr ").strip())
            if amount is None:
                problems.append(f"Row {line}: opening balance '{values['opening_balance']}' is not a number")
            else:
                party.opening_balance = sign * amount
        by_name[name.lower()] = party
        if party.phone:
            by_phone[party.phone] = party
    log(f"import {kind}s", f"{created} new, {updated} updated from {upload.filename}")
    db.session.commit()
    flash(f"Import done: {created} new, {updated} updated.", "success")
    for problem in problems[:20]:
        flash(problem, "error")
    return redirect(url_for(f"parties.{kind}s"))
