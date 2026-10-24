import os
import tempfile

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file, url_for
from sqlalchemy.exc import IntegrityError

from ..backup import BackupError, default_folder, run_backup, write_backup
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
    return render_template("settings/index.html", slabs=TaxSlab.query.order_by(TaxSlab.rate).all(),
                           default_backup_dir=default_folder(current_app))


def _admin_only():
    # A backup holds every bill and the users' password hashes.
    if not g.user.is_admin:
        abort(403)


@bp.route("/backup", methods=["POST"])
def backup_settings():
    _admin_only()
    store = Setting.get()
    folder = request.form.get("backup_dir", "").strip().strip('"')
    hours = request.form.get("backup_hours", type=int)
    keep = request.form.get("backup_keep", type=int)
    if folder and not os.path.isabs(folder):
        flash(r"Enter the full folder path, e.g. G:\My Drive\P MART Backups", "error")
    elif hours not in (0, 1, 3, 6, 12, 24) or not keep or not 1 <= keep <= 365:
        flash("Choose how often to back up and keep between 1 and 365 copies.", "error")
    else:
        try:
            if folder:
                os.makedirs(folder, exist_ok=True)
        except OSError as e:
            flash(f"Can't use that folder: {e}", "error")
        else:
            store.backup_dir, store.backup_hours, store.backup_keep = folder or None, hours, keep
            db.session.commit()
            flash("Backup settings saved.", "success")
    return redirect(url_for("settings.index", _anchor="backup"))


@bp.route("/backup/now", methods=["POST"])
def backup_now():
    _admin_only()
    ok, note = run_backup(current_app)
    flash(note, "success" if ok else "error")
    return redirect(url_for("settings.index", _anchor="backup"))


@bp.route("/backup/download")
def backup_download():
    _admin_only()
    try:
        path = write_backup(current_app, tempfile.mkdtemp(prefix="billing-download-"), keep=1)
    except BackupError as e:
        flash(str(e), "error")
        return redirect(url_for("settings.index", _anchor="backup"))
    return send_file(path, as_attachment=True, download_name=os.path.basename(path),
                     mimetype="application/vnd.sqlite3")


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
