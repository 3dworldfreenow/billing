from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..models import PAYMENT_MODES, Party, PartyPayment, db
from ..services import parse_decimal

bp = Blueprint("parties", __name__)
LABELS = {"customer": "Customers", "supplier": "Suppliers"}


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
        if not party.name:
            flash("Name is required.", "error")
        else:
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
                                    note=request.form.get("note", "").strip() or None))
        db.session.commit()
        word = "Received" if party.kind == "customer" else "Paid"
        flash(f"{word} ₹{amount} {'from' if party.kind == 'customer' else 'to'} {party.name}.", "success")
    return redirect(url_for("parties.detail", party_id=party.id))
