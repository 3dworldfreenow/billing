from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from ..models import Setting, TaxSlab, db
from ..services import parse_decimal

bp = Blueprint("settings", __name__, url_prefix="/settings")


@bp.route("/", methods=["GET", "POST"])
def index():
    store = Setting.get()
    if request.method == "POST":
        for field in ("store_name", "address", "phone", "gstin", "bill_footer", "upi_id"):
            setattr(store, field, request.form.get(field, "").strip() or None)
        store.store_name = store.store_name or "My Mart"
        store.gst_on_sales = request.form.get("gst_on_sales") == "1"
        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("settings.index"))
    return render_template("settings/index.html", slabs=TaxSlab.query.order_by(TaxSlab.rate).all())


@bp.route("/slabs", methods=["POST"])
def add_slab():
    rate = parse_decimal(request.form.get("rate", ""))
    name = request.form.get("name", "").strip()
    if rate is None or not 0 <= rate <= 100:
        flash("GST rate must be between 0 and 100.", "error")
    else:
        db.session.add(TaxSlab(rate=rate, name=name or f"GST {rate.normalize():f}%"))
        try:
            db.session.commit()
            flash("Tax slab added.", "success")
        except IntegrityError:
            db.session.rollback()
            flash(f"A {rate.normalize():f}% slab already exists.", "error")
    return redirect(url_for("settings.index", _anchor="slabs"))


@bp.route("/slabs/<int:slab_id>/toggle", methods=["POST"])
def toggle_slab(slab_id):
    slab = db.get_or_404(TaxSlab, slab_id)
    slab.active = not slab.active
    db.session.commit()
    return redirect(url_for("settings.index", _anchor="slabs"))
