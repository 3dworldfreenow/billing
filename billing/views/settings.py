import os
import secrets
import tempfile

from flask import (Blueprint, Response, abort, current_app, flash, g, redirect, render_template, request, send_file,
                   session, url_for)
from sqlalchemy.exc import IntegrityError

from ..backup import BackupError, default_folder, run_backup, write_backup
from ..models import AuditLog, Setting, TaxSlab, User, db
from ..services import log, parse_decimal

bp = Blueprint("settings", __name__, url_prefix="/settings")
SECTIONS = {"store": "Store", "gst": "GST slabs", "devices": "Scale & printer", "backup": "Backup & Google Drive",
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
    elif section == "backup":
        context["default_backup_dir"] = default_folder(current_app)
        context["redirect_uri"] = url_for("settings.drive_callback", _external=True)
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


# --- Backup & Google Drive (admin) ------------------------------------------------------


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
    from ..drive import XLSX, export_all, export_filename

    log("export all data")
    db.session.commit()
    return Response(export_all(), mimetype=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{export_filename()}"'})


@bp.route("/drive", methods=["POST"])
def drive_settings():
    _admin_only()
    store = Setting.get()
    client_id = request.form.get("drive_client_id", "").strip()
    secret = request.form.get("drive_client_secret", "").strip()
    if client_id != (store.drive_client_id or "") or (secret and secret != store.drive_client_secret):
        store.drive_refresh_token = None  # new client: connect again
    store.drive_client_id = client_id or None
    if secret:
        store.drive_client_secret = secret
    store.drive_auto = request.form.get("drive_auto") == "1"
    db.session.commit()
    flash("Google Drive settings saved.", "success")
    return _back("backup")


@bp.route("/drive/connect", methods=["POST"])
def drive_connect():
    _admin_only()
    from ..drive import DriveError, auth_url

    session["drive_state"] = secrets.token_urlsafe(24)
    try:
        return redirect(auth_url(Setting.get(), url_for("settings.drive_callback", _external=True),
                                 session["drive_state"]))
    except DriveError as e:
        flash(str(e), "error")
        return _back("backup")


@bp.route("/drive/callback")
def drive_callback():
    _admin_only()
    from ..drive import DriveError, finish_connect

    if request.args.get("error"):
        flash(f"Google Drive not connected: {request.args['error']}", "error")
    elif not request.args.get("state") or request.args.get("state") != session.pop("drive_state", None):
        flash("Connection expired. Click Connect again.", "error")
    else:
        try:
            finish_connect(Setting.get(), request.args.get("code", ""),
                           url_for("settings.drive_callback", _external=True))
            log("google drive connected")
            db.session.commit()
            flash("Google Drive connected. Click “Upload to Google Drive now” to test it.", "success")
        except DriveError as e:
            db.session.rollback()
            flash(str(e), "error")
    return _back("backup")


@bp.route("/drive/upload", methods=["POST"])
def drive_upload():
    _admin_only()
    from ..drive import upload_now

    ok, note = upload_now(current_app)
    flash(note, "success" if ok else "error")
    return _back("backup")


@bp.route("/drive/disconnect", methods=["POST"])
def drive_disconnect():
    _admin_only()
    store = Setting.get()
    store.drive_refresh_token = store.drive_folder_id = None
    store.drive_auto = False
    log("google drive disconnected")
    db.session.commit()
    flash("Google Drive disconnected.", "success")
    return _back("backup")
