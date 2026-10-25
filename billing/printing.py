"""Printing receipts straight to a thermal printer with ESC/POS commands: over the network
(most LAN receipt printers listen on port 9100) or to a Windows printer (needs pywin32).
Also kicks the cash drawer connected to the printer."""

import socket
import textwrap

from .models import D, Setting

ESC, GS = b"\x1b", b"\x1d"
INIT = ESC + b"@"
BOLD_ON, BOLD_OFF = ESC + b"E\x01", ESC + b"E\x00"
CENTER, LEFT = ESC + b"a\x01", ESC + b"a\x00"
BIG, NORMAL = GS + b"!\x11", GS + b"!\x00"
CUT = GS + b"V\x42\x03"  # feed a little and cut
DRAWER = ESC + b"p\x00\x19\xfa"  # pulse pin 2 (standard drawer port)


class PrinterError(Exception):
    pass


def _text(value):
    # Receipt printers use a basic code page: no ₹ or Indian scripts.
    return str(value).replace("₹", "Rs.").encode("cp437", errors="replace")


def _num(value):
    return f"{D(value).normalize():f}"


def _amt(value):
    return f"{D(value):,.2f}"


def qr_bytes(data, size=6):
    """ESC/POS native QR code (GS ( k), supported by most Epson-compatible receipt printers."""
    payload = data.encode()
    store = len(payload) + 3

    def cmd(fn, body):
        n = len(body) + 2
        return GS + b"(k" + bytes([n % 256, n // 256, 49, fn]) + body

    return (cmd(65, b"\x32\x00") + cmd(67, bytes([size])) + cmd(69, b"\x31")  # model 2, module size, level M
            + GS + b"(k" + bytes([store % 256, store // 256, 49, 80, 48]) + payload + cmd(81, b"\x30"))


def receipt_bytes(sale, width, link=None):
    """The bill laid out for a `width`-character thermal printer."""
    store = Setting.get()
    out = [INIT, CENTER, BOLD_ON, BIG, _text(store.store_name), b"\n", NORMAL, BOLD_OFF]
    for line in filter(None, [store.address, f"Ph: {store.phone}" if store.phone else None,
                              f"GSTIN: {store.gstin}" if store.gstin else None]):
        for part in textwrap.wrap(line, width):
            out += [_text(part), b"\n"]
    title = "CREDIT NOTE" if sale.is_return else ("TAX INVOICE" if sale.taxed and store.gstin else
                                                    "BILL OF SUPPLY" if store.gstin else "CASH MEMO")
    out += [BOLD_ON, _text(title + (" - CANCELLED" if sale.status == "cancelled" else "")), BOLD_OFF, b"\n", LEFT]

    def row(left, right):
        return _text(left[: max(width - len(right) - 1, 1)].ljust(width - len(right)) + right) + b"\n"

    rule = _text("-" * width) + b"\n"
    out += [row(f"{'CN' if sale.is_return else 'Bill'}: {sale.doc_no}", f"{sale.created_at:%d-%m-%Y %I:%M%p}"),
            _text(sale.customer_name[:width]), b"\n", rule]
    for item in sale.items:
        for part in textwrap.wrap(item.name, width) or [""]:
            out += [_text(part), b"\n"]
        mrp = f"MRP {_num(item.mrp)}" if D(item.mrp) > D(item.rate) else ""
        out.append(row(f"  {mrp}", f"{_num(item.qty)} x {_num(item.rate)}  {_amt(item.amount)}"))
        if item.promo_note:
            out.append(_text(f"  Offer: {item.promo_note}"[:width]) + b"\n")
    out += [rule, row(f"Items {len(sale.items)} Qty {_num(sale.total_qty)}", _amt(sale.gross))]
    for label, value in (("Discount", sale.discount), (f"Offer {sale.promo_note or ''}", sale.promo_discount),
                         (f"Points ({sale.points_redeemed})", sale.redeem_amount), ("Round off", sale.round_off)):
        if D(value):
            out.append(row(label, _amt(-D(value) if label != "Round off" else value)))
    out += [BOLD_ON, BIG, row("REFUND" if sale.is_return else "NET", f"Rs.{_amt(abs(D(sale.net)))}"[: width // 2]),
            NORMAL, BOLD_OFF]
    out.append(row(sale.payment_mode.upper(), f"Paid {_amt(sale.paid)}"))
    if sale.due > 0:
        out.append(row("Balance due", _amt(sale.due)))
    if sale.taxed and D(sale.tax):
        out += [rule, row("GST%  Taxable", "CGST     SGST")]
        for rate, (taxable, cgst, sgst) in sale.tax_summary().items():
            if cgst + sgst:
                out.append(row(f"{_num(rate)}%  {_amt(taxable)}", f"{_amt(cgst)} {_amt(sgst):>8}"))
    out.append(CENTER)
    if sale.savings > 0 and not sale.is_return:
        out += [rule, BOLD_ON, _text(f"You saved Rs.{_amt(sale.savings)}!"), BOLD_OFF, b"\n"]
    if sale.points_earned and sale.party:
        out += [_text(f"Points earned {sale.points_earned}, balance {sale.party.points}"), b"\n"]
    if store.bill_footer:
        out += [rule, _text(store.bill_footer), b"\n"]
    if sale.cashier_name:
        out += [_text(f"Cashier: {sale.cashier_name}"), b"\n"]
    if link:
        out += [rule, _text("Scan to download your bill"), b"\n", qr_bytes(link), b"\n"]
    out += [b"\n\n", CUT]
    return b"".join(out)


def send(data, store=None):
    store = store or Setting.get()
    address = (store.printer_address or "").strip()
    if store.printer_mode == "network":
        host, _, port = address.partition(":")
        if not host:
            raise PrinterError("Enter the printer's IP address in Settings, e.g. 192.168.1.50:9100.")
        try:
            with socket.create_connection((host, int(port or 9100)), timeout=5) as conn:
                conn.sendall(data)
        except (OSError, ValueError) as e:
            raise PrinterError(f"Printer at {address} not reachable ({e}).")
    elif store.printer_mode == "windows":
        try:
            import win32print  # pywin32, Windows only
        except ImportError:
            raise PrinterError("Windows printing needs pywin32: run  .venv\\Scripts\\pip install pywin32")
        try:
            handle = win32print.OpenPrinter(address or win32print.GetDefaultPrinter())
            try:
                win32print.StartDocPrinter(handle, 1, ("Receipt", None, "RAW"))
                win32print.StartPagePrinter(handle)
                win32print.WritePrinter(handle, data)
                win32print.EndPagePrinter(handle)
                win32print.EndDocPrinter(handle)
            finally:
                win32print.ClosePrinter(handle)
        except Exception as e:  # pywin32 raises its own error types
            raise PrinterError(f"Windows printer “{address}”: {e}")
    else:
        raise PrinterError("Direct printing is off (Settings → Printer).")


def print_sale(sale, open_drawer=False, link=None):
    store = Setting.get()
    data = receipt_bytes(sale, store.printer_width, link)
    if open_drawer and store.open_drawer:
        data = DRAWER + data
    send(data, store)


def kick_drawer():
    send(INIT + DRAWER)


def test_page():
    store = Setting.get()
    width = store.printer_width
    send(INIT + CENTER + BOLD_ON + _text(store.store_name) + BOLD_OFF + b"\n" + _text("Printer test OK") + b"\n"
         + LEFT + _text("-" * width) + b"\n" + _text(("1234567890" * 10)[:width]) + b"\n\n\n" + CUT)
