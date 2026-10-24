from datetime import date, datetime, time, timedelta
from decimal import Decimal

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func

from ..models import (EXPENSE_MODES, CashEntry, D, DayClose, Expense, Party, PartyPayment, Purchase, Sale, User, db,
                      money)
from ..services import log, parse_decimal

bp = Blueprint("cash", __name__, url_prefix="/cash")
EXPENSE_CATEGORIES = ("Rent", "Salary", "Electricity", "Transport", "Packaging", "Tea & snacks", "Repairs", "Other")


def _day():
    try:
        return date.fromisoformat(request.values.get("day", ""))
    except ValueError:
        return date.today()


def _sum(query):
    return D(query.scalar())


def register(day, user_id=None):
    """Everything that moved cash and other payments on a day, for one cashier or (None) the whole shop."""
    start = datetime.combine(day, time.min)
    end = start + timedelta(days=1)

    def scoped(model, query):
        query = query.filter(model.created_at >= start, model.created_at < end)
        return query.filter(model.user_id == user_id) if user_id else query

    sales = scoped(Sale, db.session.query(Sale.payment_mode, func.sum(Sale.net), func.sum(Sale.paid))
                   .filter(Sale.status == "saved")).group_by(Sale.payment_mode).all()
    by_mode = {mode: (D(net), D(paid)) for mode, net, paid in sales}

    def payments(kind):
        return _sum(scoped(PartyPayment, db.session.query(func.sum(PartyPayment.amount)).join(Party)
                           .filter(Party.kind == kind, PartyPayment.mode == "cash")))

    def entries(kind):
        return _sum(scoped(CashEntry, db.session.query(func.sum(CashEntry.amount)).filter(CashEntry.kind == kind)))

    r = {
        "opening": entries("opening"),
        "cash_sales": by_mode.get("cash", (Decimal(0), 0))[0],
        "credit_paid": by_mode.get("credit", (0, Decimal(0)))[1],
        "receipts": payments("customer"),
        "cash_in": entries("in"),
        "supplier_payments": payments("supplier"),
        "purchases": _sum(scoped(Purchase, db.session.query(func.sum(Purchase.paid))
                                 .filter(Purchase.status == "saved", Purchase.payment_mode.in_(("cash", "credit"))))),
        "cash_out": entries("out"),
        "expenses": _sum(scoped(Expense, db.session.query(func.sum(Expense.amount)).filter(Expense.mode == "cash"))),
        "upi": by_mode.get("upi", (Decimal(0), 0))[0],
        "card": by_mode.get("card", (Decimal(0), 0))[0],
        "credit_given": by_mode.get("credit", (Decimal(0), Decimal(0)))[0] - by_mode.get("credit", (0, Decimal(0)))[1],
    }
    r["expected"] = money(r["opening"] + r["cash_sales"] + r["credit_paid"] + r["receipts"] + r["cash_in"]
                          - r["supplier_payments"] - r["purchases"] - r["cash_out"] - r["expenses"])
    r["entries"] = scoped(CashEntry, CashEntry.query).order_by(CashEntry.id).all()
    r["expense_list"] = scoped(Expense, Expense.query).order_by(Expense.id).all()
    return r


@bp.route("/")
def index():
    day = _day()
    # Admins and managers can look at any cashier or the whole shop; others see their own drawer.
    can_pick = g.user.is_admin or g.user.can("reports")
    user_id = request.args.get("user_id", type=int) if can_pick else g.user.id
    closes = DayClose.query.order_by(DayClose.day.desc(), DayClose.id.desc()).limit(30).all()
    return render_template(
        "cash/index.html", day=day, user_id=user_id, r=register(day, user_id), closes=closes,
        users=User.query.filter_by(active=True).order_by(User.name).all() if can_pick else [],
        categories=EXPENSE_CATEGORIES, expense_modes=EXPENSE_MODES,
        closed=DayClose.query.filter_by(day=day, user_id=user_id).first(),
    )


def _back(day, user_id=None):
    return redirect(url_for("cash.index", day=day.isoformat(), user_id=user_id))


@bp.route("/entry", methods=["POST"])
def entry():
    kind = request.form.get("kind")
    amount = parse_decimal(request.form.get("amount", ""))
    if kind not in ("opening", "in", "out") or amount is None or amount <= 0:
        flash("Enter an amount greater than zero.", "error")
    else:
        note = request.form.get("note", "").strip() or None
        db.session.add(CashEntry(kind=kind, amount=amount, note=note, user_id=g.user.id))
        log("cash " + kind, f"₹{amount} {note or ''}")
        db.session.commit()
        flash({"opening": "Opening cash saved.", "in": "Cash added to drawer.", "out": "Cash taken out."}[kind],
              "success")
    return _back(date.today())


@bp.route("/expense", methods=["POST"])
def expense():
    amount = parse_decimal(request.form.get("amount", ""))
    category = request.form.get("category", "").strip()
    mode = request.form.get("mode", "cash")
    if amount is None or amount <= 0 or not category:
        flash("Enter the expense category and an amount greater than zero.", "error")
    else:
        db.session.add(Expense(category=category[:60], amount=amount, mode=mode if mode in EXPENSE_MODES else "cash",
                               note=request.form.get("note", "").strip() or None, user_id=g.user.id))
        log("expense", f"{category} ₹{amount} ({mode})")
        db.session.commit()
        flash(f"Expense {category} ₹{amount} saved.", "success")
    return _back(date.today())


@bp.route("/close", methods=["POST"])
def close():
    day = _day()
    user_id = request.form.get("user_id", type=int) or None
    if not (g.user.is_admin or g.user.can("reports")):
        user_id = g.user.id
    counted = parse_decimal(request.form.get("counted", ""))
    if counted is None or counted < 0:
        flash("Enter the cash counted in the drawer.", "error")
        return _back(day, user_id)
    expected = register(day, user_id)["expected"]
    existing = DayClose.query.filter_by(day=day, user_id=user_id).first()
    record = existing or DayClose(day=day, user_id=user_id)
    record.expected, record.counted, record.closed_by_id = expected, counted, g.user.id
    record.note = request.form.get("note", "").strip() or None
    record.created_at = datetime.now()
    db.session.add(record)
    who = db.session.get(User, user_id).name if user_id else "whole shop"
    log("day close", f"{day:%d-%m-%Y} {who}: expected ₹{expected}, counted ₹{counted}, "
                     f"difference ₹{record.difference}")
    db.session.commit()
    diff = record.difference
    flash(f"Day closed. {'Cash matches.' if not diff else ('Extra' if diff > 0 else 'Short') + f' ₹{abs(diff)}.'}",
          "success" if not diff else "error")
    return _back(day, user_id)
