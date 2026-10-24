from datetime import date, time

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..models import Party, Product, Promotion, Setting, db
from ..services import log, parse_decimal

bp = Blueprint("offers", __name__, url_prefix="/offers")
KINDS = {"percent": "% off products", "bxgy": "Buy X get Y free", "bill": "Bill value offer"}


@bp.route("/")
def index():
    customers = Party.query.filter_by(kind="customer").all()
    top = sorted(((c, c.points) for c in customers), key=lambda cp: -cp[1])[:10]
    return render_template("offers/index.html", offers=Promotion.query.order_by(Promotion.active.desc(),
                                                                                Promotion.id.desc()).all(),
                           running={p.id for p in Promotion.running()}, kinds=KINDS, top=[t for t in top if t[1]])


def _date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _time(value):
    try:
        return time.fromisoformat(value) if value else None
    except ValueError:
        return None


@bp.route("/new", methods=["GET", "POST"])
@bp.route("/<int:offer_id>", methods=["GET", "POST"])
def edit(offer_id=None):
    offer = db.get_or_404(Promotion, offer_id) if offer_id else Promotion(kind=request.args.get("kind", "percent"),
                                                                         active=True)
    if request.method == "POST":
        f = request.form
        offer.name = f.get("name", "").strip()
        offer.kind = f.get("kind") if f.get("kind") in KINDS else "percent"
        product = db.session.get(Product, f.get("product_id", type=int) or 0)
        offer.product = product
        offer.category = (f.get("category", "").strip() or None) if product is None else None
        offer.percent = parse_decimal(f.get("percent", ""), 0)
        offer.min_amount = parse_decimal(f.get("min_amount", ""), 0)
        offer.flat_amount = parse_decimal(f.get("flat_amount", ""), 0)
        offer.buy_qty = f.get("buy_qty", 0, type=int)
        offer.free_qty = f.get("free_qty", 0, type=int)
        offer.starts_on, offer.ends_on = _date(f.get("starts_on")), _date(f.get("ends_on"))
        offer.start_time, offer.end_time = _time(f.get("start_time")), _time(f.get("end_time"))
        offer.active = f.get("active") == "1"
        error = None
        if not offer.name:
            error = "Give the offer a name (it is printed on the bill)."
        elif None in (offer.percent, offer.min_amount, offer.flat_amount) or not 0 <= offer.percent <= 100:
            error = "Check the numbers: percent must be 0–100."
        elif offer.kind == "percent" and not offer.percent:
            error = "Enter the % off."
        elif offer.kind == "bxgy" and (offer.buy_qty < 1 or offer.free_qty < 1):
            error = "Enter how many to buy and how many are free."
        elif offer.kind == "bill" and not (offer.percent or offer.flat_amount):
            error = "Enter a % or a flat ₹ discount for the bill."
        if error:
            flash(error, "error")
        else:
            db.session.add(offer)
            log("offer saved", offer.summary)
            db.session.commit()
            flash(f"Offer “{offer.name}” saved.", "success")
            return redirect(url_for("offers.index"))
    categories = sorted(c for (c,) in db.session.query(Product.category).distinct() if c)
    return render_template("offers/form.html", offer=offer, kinds=KINDS, categories=categories)


@bp.route("/<int:offer_id>/toggle", methods=["POST"])
def toggle(offer_id):
    offer = db.get_or_404(Promotion, offer_id)
    offer.active = not offer.active
    log("offer " + ("on" if offer.active else "off"), offer.name)
    db.session.commit()
    return redirect(url_for("offers.index"))


@bp.route("/loyalty", methods=["POST"])
def loyalty():
    store = Setting.get()
    rate = parse_decimal(request.form.get("loyalty_rate", ""), 0)
    value = parse_decimal(request.form.get("loyalty_value", ""), 0)
    minimum = request.form.get("loyalty_min_redeem", 0, type=int)
    if rate is None or value is None or rate < 0 or value < 0 or minimum < 0:
        flash("Loyalty numbers must be zero or more.", "error")
    else:
        store.loyalty_rate, store.loyalty_value, store.loyalty_min_redeem = rate, value, minimum
        log("loyalty settings", f"{rate} points per ₹100, 1 point = ₹{value}, min {minimum}")
        db.session.commit()
        flash("Loyalty settings saved.", "success")
    return redirect(url_for("offers.index", _anchor="loyalty"))
