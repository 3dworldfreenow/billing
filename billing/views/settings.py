import os
import tempfile
from datetime import datetime

from flask import (Blueprint, Response, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   send_file, url_for)
from sqlalchemy.exc import IntegrityError

from ..backup import BackupError, default_folder, run_backup, write_backup
from ..models import AuditLog, Party, Setting, TaxSlab, User, db
from ..services import log, parse_decimal

bp = Blueprint("settings", __name__, url_prefix="/settings")
SECTIONS = {"store": "Store", "gst": "GST slabs", "shop": "Online shop", "devices": "Scale & printer", "backup": "Backup & export",
            "audit": "Audit log"}
ADMIN_SECTIONS = {"backup", "audit"}  # backups hold every bill and the users' password hashes


def _admin_only():
    if not g.user.is_admin:
        abort(403)


def _back(section):
    return redirect(url_for("settings.index", section=section))


@bp.route("/")
@bp.route("/<section>")
def index(section="store"):
    if section not in SECTIONS:
        abort(404)
    if section in ADMIN_SECTIONS:
        _admin_only()
    context = {}
    if section == "gst":
        context["slabs"] = TaxSlab.query.order_by(TaxSlab.rate).all()
    elif section == "shop":
        from ..ai import DEFAULT_MODEL

        context["shop_url"] = url_for("shop.catalog" if Setting.get().shop_public else "shop.login", _external=True)
        context["default_model"] = DEFAULT_MODEL
        context["approved"] = Party.query.filter_by(kind="customer", shop_access=True).order_by(Party.name).all()
    elif section == "backup":
        context["default_backup_dir"] = default_folder(current_app)
    elif section == "audit":
        query = AuditLog.query
        if request.args.get("user_id", type=int):
            query = query.filter_by(user_id=request.args.get("user_id", type=int))
        if request.args.get("action"):
            query = query.filter_by(action=request.args["action"])
        context.update(entries=query.order_by(AuditLog.id.desc()).limit(500).all(),
                       users=User.query.order_by(User.name).all(),
                       actions=[a for (a,) in db.session.query(AuditLog.action).distinct().order_by(AuditLog.action)])
    sections = {k: v for k, v in SECTIONS.items() if k not in ADMIN_SECTIONS or g.user.is_admin}
    return render_template(f"settings/{section}.html", section=section, sections=sections, **context)


@bp.route("/store", methods=["POST"])
def save_store():
    store = Setting.get()
    for field in ("store_name", "address", "phone", "gstin", "bill_footer", "upi_id"):
        setattr(store, field, request.form.get(field, "").strip() or None)
    store.store_name = store.store_name or "My Mart"
    gst = request.form.get("gst_on_sales") == "1"
    if gst != store.gst_on_sales:
        log("settings", f"Charge GST on sales: {'on' if gst else 'off'}")
    store.gst_on_sales = gst
    db.session.commit()
    flash("Store settings saved.", "success")
    return _back("store")


@bp.route("/shop", methods=["POST"])
def save_shop():
    store = Setting.get()
    minimum = parse_decimal(request.form.get("shop_min_order", ""), 0)
    if minimum is None or minimum < 0:
        flash("Minimum order must be zero or more.", "error")
        return _back("shop")
    enabled = request.form.get("shop_enabled") == "1"
    if enabled != store.shop_enabled:
        log("settings", f"Online shop {'opened' if enabled else 'closed'}")
    store.shop_enabled, store.shop_min_order = enabled, minimum
    store.shop_note = request.form.get("shop_note", "").strip() or None
    public = request.form.get("shop_public") == "1"
    if public != store.shop_public:
        log("settings", f"Online shop SEO mode {'on' if public else 'off'}")
    store.shop_public = public
    if "shop_seo_title" in request.form:
        store.shop_seo_title = request.form.get("shop_seo_title", "").strip()[:80] or None
        store.shop_seo_description = " ".join(request.form.get("shop_seo_description", "").split())[:200] or None
    db.session.commit()
    flash("Online shop settings saved.", "success")
    return _back("shop")


@bp.route("/ai", methods=["POST"])
def save_ai():
    _admin_only()
    store = Setting.get()
    key = request.form.get("gemini_api_key", "").strip()
    if request.form.get("remove_key") == "1":
        store.gemini_api_key = None
        log("settings", "Gemini key removed")
    elif key:
        store.gemini_api_key = key
        log("settings", "Gemini key saved")
    store.gemini_model = request.form.get("gemini_model", "").strip()[:80] or None
    if store.gemini_api_key:
        ok, message = _test_gemini(store)
        flash(f"AI writing settings saved. {message}", "success" if ok else "error")
    else:
        store.gemini_checked_at = store.gemini_status = None
        flash("AI writing settings saved.", "success")
    db.session.commit()
    return _back("shop")


def _test_gemini(store):
    """Try the saved key and model, remember the result, and describe it for the admin."""
    from ..ai import DEFAULT_MODEL, AIError, test_connection

    model = store.gemini_model or DEFAULT_MODEL
    try:
        test_connection(store.gemini_api_key, model)
        store.gemini_status, ok = "OK", True
        message = f"Connected: {model} is working."
    except AIError as e:
        store.gemini_status, ok = str(e)[:300], False
        message = f"Connection test failed: {e}"
    store.gemini_checked_at = datetime.now()
    return ok, message


@bp.route("/ai/test", methods=["POST"])
def ai_test():
    _admin_only()
    store = Setting.get()
    ok, message = _test_gemini(store)
    db.session.commit()
    return jsonify(ok=ok, message=message, checked=store.gemini_checked_at.strftime("%d %b, %I:%M %p"))


@bp.route("/ai/models", methods=["POST"])
def ai_models():
    _admin_only()
    from ..ai import AIError, list_models

    try:
        return jsonify(models=list_models(Setting.get().gemini_api_key or ""))
    except AIError as e:
        return jsonify(error=str(e)), 502


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
    return _back("gst")


@bp.route("/slabs/<int:slab_id>/toggle", methods=["POST"])
def toggle_slab(slab_id):
    slab = db.get_or_404(TaxSlab, slab_id)
    slab.active = not slab.active
    db.session.commit()
    return _back("gst")


@bp.route("/devices", methods=["POST"])
def save_devices():
    store = Setting.get()
    f = request.form
    prefix = f.get("scale_prefix", "").strip()
    digits = f.get("scale_item_digits", 5, type=int)
    if prefix and (not prefix.isdigit() or len(prefix) > 3 or not 3 <= digits <= 6 or len(prefix) + digits > 9):
        flash("Scale barcode: prefix of 1–3 digits and an item code of 3–6 digits.", "error")
        return _back("devices")
    store.scale_prefix, store.scale_item_digits = prefix or None, digits
    store.scale_value = "price" if f.get("scale_value") == "price" else "weight"
    store.printer_mode = f.get("printer_mode") if f.get("printer_mode") in ("browser", "network", "windows") \
        else "browser"
    store.printer_address = f.get("printer_address", "").strip() or None
    store.printer_width = 32 if f.get("printer_width") == "32" else 48
    store.auto_print = f.get("auto_print") == "1"
    store.open_drawer = f.get("open_drawer") == "1"
    db.session.commit()
    flash("Scale and printer settings saved.", "success")
    return _back("devices")


@bp.route("/devices/test", methods=["POST"])
def test_printer():
    from ..printing import PrinterError, kick_drawer, test_page

    try:
        if request.form.get("what") == "drawer":
            kick_drawer()
        else:
            test_page()
        flash("Sent to the printer.", "success")
    except PrinterError as e:
        flash(str(e), "error")
    return _back("devices")


# --- Backup & export (admin) ------------------------------------------------------


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
    return _back("backup")


@bp.route("/backup/now", methods=["POST"])
def backup_now():
    _admin_only()
    ok, note = run_backup(current_app)
    flash(note, "success" if ok else "error")
    return _back("backup")


@bp.route("/backup/download")
def backup_download():
    _admin_only()
    try:
        path = write_backup(current_app, tempfile.mkdtemp(prefix="billing-download-"), keep=1)
    except BackupError as e:
        flash(str(e), "error")
        return _back("backup")
    log("download backup")
    db.session.commit()
    return send_file(path, as_attachment=True, download_name=os.path.basename(path),
                     mimetype="application/vnd.sqlite3")


@bp.route("/export-all")
def export_all():
    """All data as one Excel workbook (one sheet per table)."""
    _admin_only()
    from ..export_data import XLSX, export_all, export_filename

    log("export all data")
    db.session.commit()
    return Response(export_all(), mimetype=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{export_filename()}"'})
