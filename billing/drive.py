"""Export all data to Excel, and upload backups straight to Google Drive.

Google Drive uses OAuth: the shop creates a free OAuth client in Google Cloud once
(Settings shows the steps), connects its Google account, and the app keeps a refresh
token. The "drive.file" scope only lets the app see files it created itself."""

import io
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, time
from decimal import Decimal

from openpyxl import Workbook

from .backup import write_backup
from .models import (AuditLog, Batch, CashEntry, DayClose, Expense, Order, OrderItem, Party, PartyPayment, Product,
                     Promotion, Purchase, PurchaseItem, Sale, SaleItem, Setting, StockAdjustment, db)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
FILES_URL = "https://www.googleapis.com/drive/v3/files"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,name"
SCOPE = "https://www.googleapis.com/auth/drive.file"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Sheet name -> model. Users and settings are left out: they hold passwords and keys.
EXPORT = [
    ("Products", Product), ("Batches", Batch), ("Customers & suppliers", Party), ("Bills & credit notes", Sale),
    ("Bill items", SaleItem), ("Purchases & orders", Purchase), ("Purchase items", PurchaseItem),
    ("Party payments", PartyPayment), ("Expenses", Expense), ("Cash entries", CashEntry), ("Day close", DayClose),
    ("Stock adjustments", StockAdjustment), ("Offers", Promotion), ("Online orders", Order),
    ("Online order items", OrderItem), ("Audit log", AuditLog),
]


class DriveError(Exception):
    pass


def _cell(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, time):
        return value.strftime("%H:%M")
    return value


def export_all():
    """Every table (except users/settings) as one Excel workbook, one sheet each."""
    wb = Workbook(write_only=True)
    for title, model in EXPORT:
        ws = wb.create_sheet(title[:31])
        columns = [c for c in model.__table__.columns if not c.name.endswith("password_hash")]  # never export these
        ws.append([c.name for c in columns])
        for row in db.session.query(*columns).yield_per(1000):
            ws.append([_cell(v) for v in row])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_filename():
    return f"{Setting.get().store_name}-all-data-{datetime.now():%Y-%m-%d_%H-%M}.xlsx".replace(" ", "-")


# --- OAuth ------------------------------------------------------------------------


def _post_form(url, fields):
    body = urllib.parse.urlencode(fields).encode()
    return _call(urllib.request.Request(url, data=body, method="POST",
                                        headers={"Content-Type": "application/x-www-form-urlencoded"}))


def _call(req):
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise DriveError(f"Google said {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise DriveError(f"Can't reach Google ({e.reason}). Check the internet connection.")


def auth_url(store, redirect_uri, state):
    if not (store.drive_client_id and store.drive_client_secret):
        raise DriveError("Enter the Google OAuth client ID and secret first.")
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": store.drive_client_id, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state,
    })


def finish_connect(store, code, redirect_uri):
    tokens = _post_form(TOKEN_URL, {
        "code": code, "client_id": store.drive_client_id, "client_secret": store.drive_client_secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    })
    if not tokens.get("refresh_token"):
        raise DriveError("Google didn't return a refresh token. Remove the app's access in your Google "
                         "account and connect again.")
    store.drive_refresh_token = tokens["refresh_token"]
    store.drive_folder_id = None


def _access_token(store):
    if not store.drive_refresh_token:
        raise DriveError("Google Drive is not connected.")
    tokens = _post_form(TOKEN_URL, {
        "client_id": store.drive_client_id, "client_secret": store.drive_client_secret,
        "refresh_token": store.drive_refresh_token, "grant_type": "refresh_token",
    })
    return tokens["access_token"]


def _folder(store, token):
    """The app's backup folder in Drive (created on first upload, again if it was deleted)."""
    auth = {"Authorization": f"Bearer {token}"}
    if store.drive_folder_id:
        try:
            info = _call(urllib.request.Request(f"{FILES_URL}/{store.drive_folder_id}?fields=id,trashed", headers=auth))
            if not info.get("trashed"):
                return store.drive_folder_id
        except DriveError:
            pass
    meta = {"name": f"{store.store_name} Backups", "mimeType": "application/vnd.google-apps.folder"}
    folder = _call(urllib.request.Request(FILES_URL, data=json.dumps(meta).encode(), method="POST",
                                          headers={**auth, "Content-Type": "application/json"}))
    store.drive_folder_id = folder["id"]
    return folder["id"]


def _upload(token, folder_id, name, data, mimetype):
    boundary = uuid.uuid4().hex
    meta = json.dumps({"name": name, "parents": [folder_id]}).encode()
    body = b"".join([
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(), meta,
        f"\r\n--{boundary}\r\nContent-Type: {mimetype}\r\n\r\n".encode(), data, f"\r\n--{boundary}--\r\n".encode(),
    ])
    return _call(urllib.request.Request(UPLOAD_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": f"multipart/related; boundary={boundary}",
    }))


def upload_now(app):
    """Upload a fresh database backup and the all-data Excel export to Drive. Records the result."""
    store = Setting.get()
    try:
        token = _access_token(store)
        folder = _folder(store, token)
        path = write_backup(app, tempfile.mkdtemp(prefix="billing-drive-"), keep=1)
        with open(path, "rb") as f:
            _upload(token, folder, os.path.basename(path), f.read(), "application/vnd.sqlite3")
        _upload(token, folder, export_filename(), export_all(), XLSX)
        store.last_drive_note = (f"Uploaded {os.path.basename(path)} and the Excel export to "
                                 f"“{store.store_name} Backups”")
        ok = True
    except (DriveError, OSError, KeyError) as e:
        store.last_drive_note = f"Upload FAILED: {e}"
        ok = False
    store.last_drive_at = datetime.now()
    db.session.commit()
    return ok, store.last_drive_note

