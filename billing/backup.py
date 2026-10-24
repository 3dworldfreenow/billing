"""Database backups: a consistent copy of the SQLite file, written to a folder
(for example one synced by Google Drive for desktop) on a schedule or on demand."""

import glob
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta

from .models import Setting, db

PREFIX = "billing-backup-"


class BackupError(Exception):
    pass


def database_file(app):
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    if not uri.startswith("sqlite:///"):
        raise BackupError("Backups work with the built-in database only.")
    path = uri[len("sqlite:///"):].split("?")[0]
    if not os.path.exists(path):
        raise BackupError("Database file not found.")
    return path


def default_folder(app):
    return os.path.join(app.instance_path, "backups")


def write_backup(app, folder, keep=30):
    """Copy the live database safely (SQLite's backup API works while the app is in use).
    Writes to a .part file first so Drive never uploads a half-written copy."""
    source = database_file(app)
    os.makedirs(folder, exist_ok=True)
    name = f"{PREFIX}{datetime.now():%Y-%m-%d_%H-%M-%S}.db"
    final = os.path.join(folder, name)
    partial = final + ".part"
    src, dst = sqlite3.connect(source), sqlite3.connect(partial)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()
    os.replace(partial, final)
    if keep and keep > 0:
        for old in sorted(glob.glob(os.path.join(folder, PREFIX + "*.db")))[:-keep]:
            os.remove(old)
    return final


def run_backup(app):
    """Back up to the configured folder and record the result in settings."""
    store = Setting.get()
    folder = store.backup_dir or default_folder(app)
    try:
        path = write_backup(app, folder, store.backup_keep)
        store.last_backup_note = f"Saved {os.path.basename(path)} to {folder}"
        ok = True
    except (OSError, sqlite3.Error, BackupError) as e:
        store.last_backup_note = f"Backup FAILED: {e}"
        ok = False
    store.last_backup_at = datetime.now()
    db.session.commit()
    return ok, store.last_backup_note


def start_scheduler(app, check_every=300):
    """Background thread: back up whenever the last backup is older than the chosen interval."""

    def loop():
        while True:
            try:
                with app.app_context():
                    store = Setting.get()
                    due = store.backup_hours and (
                        store.last_backup_at is None
                        or datetime.now() - store.last_backup_at >= timedelta(hours=store.backup_hours)
                    )
                    if due:
                        run_backup(app)
                        if store.drive_auto and store.drive_refresh_token:
                            from .drive import upload_now

                            upload_now(app)
            except Exception as e:  # never let the thread die; the settings page shows failures
                app.logger.warning("Scheduled backup failed: %s", e)
            time.sleep(check_every)

    threading.Thread(target=loop, name="backup", daemon=True).start()
