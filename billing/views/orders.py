"""Online orders from approved customers: call to confirm, pack, deliver, and make the bill."""

from datetime import datetime

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func

from ..models import ORDER_STEPS, Order, Promotion, Sale, Setting, db
from ..services import log
from .sales import whatsapp_url

bp = Blueprint("orders", __name__, url_prefix="/orders")
ACTIVE = ("new", "confirmed", "packed", "out")


@bp.app_context_processor
def new_orders():
    """Red count of new orders in the menu."""
    user = g.get("user")
    if user is None or not user.can("orders"):
        return {}
    return {"new_orders": Order.query.filter_by(status="new").count()}


@bp.route("/")
def index():
    show = request.args.get("show", "active")
    counts = dict(db.session.query(Order.status, func.count()).filter(Order.status != "cart").group_by(Order.status))
    query = Order.query.filter(Order.status.in_(ACTIVE) if show == "active" else Order.status == show)
    return render_template("orders/list.html", orders=query.order_by(Order.placed_at.desc()).limit(300).all(),
                           show=show, counts=counts, steps=ORDER_STEPS, active_count=sum(counts.get(s, 0) for s in ACTIVE))


@bp.route("/<int:order_id>")
def detail(order_id):
    order = db.get_or_404(Order, order_id)
    if order.status == "cart":
        return redirect(url_for("orders.index"))
    text = (f"Namaste {order.party.name}, your order {order.number} at {Setting.get().store_name} is "
            f"*{order.status_label}*. Total about ₹{order.total}.")
    return render_template("orders/detail.html", order=order, steps=ORDER_STEPS,
                           whatsapp=whatsapp_url(order.party.phone, text))


@bp.route("/<int:order_id>/status", methods=["POST"])
def set_status(order_id):
    order = db.get_or_404(Order, order_id)
    status = request.form.get("status")
    if status not in ORDER_STEPS and status != "cancelled" or order.status == "cart":
        flash("Unknown status.", "error")
    else:
        order.status, order.updated_at = status, datetime.now()
        order.staff_note = request.form.get("staff_note", "").strip() or order.staff_note
        log("order " + status, f"Order {order.number}, {order.party.name}")
        db.session.commit()
        flash(f"Order {order.number}: {order.status_label}.", "success")
    return redirect(url_for("orders.detail", order_id=order.id))


@bp.route("/<int:order_id>/bill", methods=["POST"])
def make_bill(order_id):
    """Start a bill with the order's items (at today's rates, with running offers). Stock changes when it's saved."""
    order = db.get_or_404(Order, order_id)
    if order.sale and order.sale.status != "cancelled":
        return redirect(url_for("sales.bill", sale_id=order.sale_id))
    sale = Sale(user=g.user, party=order.party, taxed=Setting.get().gst_on_sales)
    db.session.add(sale)
    for item in order.items:
        sale.add_product(item.product, item.qty)
    sale.compute(Promotion.running())
    order.sale = sale
    if order.status in ("new", "confirmed"):
        order.status, order.updated_at = "packed", datetime.now()
    db.session.commit()
    flash(f"Bill started from order {order.number}. Check items, then save.", "success")
    return redirect(url_for("sales.bill", sale_id=sale.id))
