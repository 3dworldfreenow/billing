"""Export all data to one Excel workbook (to keep a copy, e.g. upload it to Google Drive)."""

import io
from datetime import datetime, time
from decimal import Decimal

from openpyxl import Workbook

from .models import (AuditLog, Batch, CashEntry, DayClose, Expense, Order, OrderItem, Party, PartyPayment, Product,
                     Promotion, Purchase, PurchaseItem, Sale, SaleItem, Setting, StockAdjustment, db)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Sheet name -> model. Users and settings are left out: they hold passwords and keys.
EXPORT = [
    ("Products", Product), ("Batches", Batch), ("Customers & suppliers", Party), ("Bills & credit notes", Sale),
    ("Bill items", SaleItem), ("Purchases & orders", Purchase), ("Purchase items", PurchaseItem),
    ("Party payments", PartyPayment), ("Expenses", Expense), ("Cash entries", CashEntry), ("Day close", DayClose),
    ("Stock adjustments", StockAdjustment), ("Offers", Promotion), ("Online orders", Order),
    ("Online order items", OrderItem), ("Audit log", AuditLog),
]


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
