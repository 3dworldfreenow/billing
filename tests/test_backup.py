import os
import sqlite3

from billing import create_app
from billing.backup import run_backup
from billing.models import Setting, db


def file_app(tmp_path):
    return create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'shop.db'}"})


def admin_client(app):
    client = app.test_client()
    client.post("/setup", data={"name": "Owner", "username": "admin", "password": "secret123"})
    return client


def test_older_database_gets_new_columns(tmp_path):
    # A shop database from before backups existed: setting table without the backup columns.
    path = tmp_path / "shop.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE setting (id INTEGER PRIMARY KEY, store_name VARCHAR(120) NOT NULL, address TEXT, "
                "phone VARCHAR(40), gstin VARCHAR(20), gst_on_sales BOOLEAN NOT NULL, bill_footer VARCHAR(200), "
                "upi_id VARCHAR(80))")
    con.execute("INSERT INTO setting (id, store_name, gst_on_sales) VALUES (1, 'P MART', 1)")
    con.commit()
    con.close()
    app = file_app(tmp_path)
    with app.app_context():
        store = Setting.get()
        assert store.store_name == "P MART"  # data kept
        assert (store.backup_hours, store.backup_keep, store.backup_dir) == (6, 30, None)


def test_backup_now_writes_to_folder_and_keeps_latest(tmp_path):
    app = file_app(tmp_path)
    with app.app_context():
        client = admin_client(app)
        drive = tmp_path / "My Drive" / "P MART Backups"
        resp = client.post("/settings/backup", data={"backup_dir": str(drive), "backup_hours": "6", "backup_keep": "2"},
                           follow_redirects=True)
        assert b"Backup settings saved" in resp.data
        for day in ("2026-10-01", "2026-10-02", "2026-10-03"):  # older backups already in the folder
            (drive / f"billing-backup-{day}_09-00-00.db").write_bytes(b"old")
        assert b"Saved billing-backup-" in client.post("/settings/backup/now", follow_redirects=True).data
        files = sorted(p.name for p in drive.iterdir())
        assert len(files) == 2 and files[0] == "billing-backup-2026-10-03_09-00-00.db"
        backup = sqlite3.connect(drive / files[1])
        assert backup.execute("SELECT username FROM user").fetchone() == ("admin",)
        backup.close()


def test_download_backup_and_admin_only(tmp_path):
    app = file_app(tmp_path)
    with app.app_context():
        client = admin_client(app)
        resp = client.get("/settings/backup/download")
        assert resp.status_code == 200 and resp.data.startswith(b"SQLite format 3")
        client.post("/users/new", data={"name": "Manager", "username": "manager", "password": "manage123",
                                        "perm_settings": "1", "active": "1"})
        other = app.test_client()
        other.post("/login", data={"username": "manager", "password": "manage123"})
        assert other.get("/settings/").status_code == 200
        assert b'id="backup"' not in other.get("/settings/").data
        assert other.get("/settings/backup/download").status_code == 403
        assert other.post("/settings/backup/now").status_code == 403


def test_failed_backup_is_reported(tmp_path):
    app = file_app(tmp_path)
    with app.app_context():
        blocker = tmp_path / "not-a-folder"
        blocker.write_text("x")
        Setting.get().backup_dir = str(blocker / "sub")
        db.session.commit()
        ok, note = run_backup(app)
        assert not ok and note.startswith("Backup FAILED")
        assert "FAILED" in Setting.get().last_backup_note


def test_relative_folder_rejected(tmp_path):
    app = file_app(tmp_path)
    with app.app_context():
        client = admin_client(app)
        resp = client.post("/settings/backup", data={"backup_dir": "backups", "backup_hours": "6", "backup_keep": "30"},
                           follow_redirects=True)
        assert b"full folder path" in resp.data
