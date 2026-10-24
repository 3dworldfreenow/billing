"""Posting rules: what saving or cancelling a bill does to numbers, stock and prices."""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from flask import g, has_request_context
from sqlalchemy import case, or_

from .models import AuditLog, Batch, D, Product, Promotion, Sale, SaleItem, Setting, db, money


class BillError(ValueError):
    pass


def parse_decimal(value, default=None):
    if value is None:
        return default
    try:
        text = str(value).strip()
        return Decimal(text) if text else default
    except InvalidOperation:
        return None


def log(action, detail=""):
    """Record an action in the audit log (committed with the caller's changes)."""
    user = g.get("user") if has_request_context() else None
    db.session.add(AuditLog(user=user, action=action, detail=str(detail)[:500]))


# --- Finding products ---------------------------------------------------------


def product_query(text):
    """Products matching every word (name, brand, category or barcode start), best matches first."""
    query = Product.query
    words = text.split()
    for word in words:
        like = f"%{word}%"
        query = query.filter(or_(Product.name.ilike(like), Product.brand.ilike(like),
                                 Product.category.ilike(like), Product.barcode.like(f"{word}%")))
    if words:
        query = query.order_by(case((Product.name.ilike(f"{words[0]}%"), 0), else_=1))
    return query.order_by(Product.name)


def find_products(text, limit=30):
    """An exact barcode match, otherwise the best matches for the text."""
    text = text.strip()
    if not text:
        return []
    product = Product.query.filter_by(barcode=text).first()
    if product:
        return [product]
    return product_query(text).limit(limit).all()


def parse_scan(text):
    """'3*8901234' (or '3x8901234') means quantity 3 of that barcode."""
    for sep in ("*", "x", "X"):
        qty, _, code = text.partition(sep)
        qty = parse_decimal(qty)
        if code and qty is not None and qty != 0:
            return qty, code.strip()
    return Decimal(1), text.strip()


def parse_scale_barcode(code):
    """A weighing-scale label: prefix + item code (PLU) + weight in grams or price in paise
    + check digit. Returns (product, quantity) or None."""
    store = Setting.get()
    prefix = store.scale_prefix
    if not prefix or len(code) != 13 or not code.isdigit() or not code.startswith(prefix):
        return None
    start = len(prefix)
    plu = code[start:start + store.scale_item_digits]
    value = code[start + store.scale_item_digits:12]
    if not value:
        return None
    product = Product.query.filter(Product.plu.in_({plu, plu.lstrip("0") or "0"})).first()
    if product is None:
        return None
    if store.scale_value == "price":
        rate = D(product.sale_rate)
        if rate <= 0:
            return None
        return product, (Decimal(value) / 100 / rate).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return product, Decimal(value) / 1000


def resolve_scan(text):
    """What a scan or typed search means: (quantity, matching products, the code)."""
    qty, code = parse_scan(text)
    if Product.query.filter_by(barcode=code).first() is None:
        scale = parse_scale_barcode(code)
        if scale:
            return scale[1] * qty, [scale[0]], code
    return qty, find_products(code), code


def ean13_check(digits12):
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(digits12))
    return str((10 - total % 10) % 10)


def generate_barcode(product):
    """An in-store EAN-13 (prefix 200-299 is reserved for in-store use) for a product without one."""
    for prefix in range(200, 300):
        body = f"{prefix}{product.id:09d}"
        code = body + ean13_check(body)
        if not Product.query.filter(Product.barcode == code, Product.id != product.id).first():
            product.barcode = code
            return code
    raise BillError("No free barcode left.")


# --- Stock ---------------------------------------------------------------------


def ensure_batched(product):
    """Before a product's first batch, put its existing stock into an "OPENING" batch,
    so product.stock always equals the sum of its batches."""
    if not product.batches and D(product.stock) != 0:
        db.session.add(Batch(product=product, batch_no="OPENING", mrp=product.mrp, sale_rate=product.sale_rate,
                             purchase_rate=product.purchase_rate, qty=product.stock))


def change_stock(product, delta, batch=None):
    """The only place stock changes. Batched products also update a batch
    (the given one, else the one to sell first)."""
    delta = D(delta)
    if batch is None and product.batches:
        live = product.batches_in_stock
        batch = live[0] if live else product.batches[-1]
    if batch is not None:
        batch.qty = D(batch.qty) + delta
    product.stock = D(product.stock) + delta


def _purchase_batch(item):
    """The batch a purchase line goes into: matched on batch no., expiry and MRP, created when needed.
    Batches start when a line has a batch no./expiry, or brings a new MRP for stock already on hand."""
    product = item.product
    mrp = D(item.mrp) or D(product.mrp)
    new_mrp = D(product.stock) > 0 and mrp != D(product.mrp)
    if not (item.batch_no or item.expiry or product.batches or new_mrp):
        return None
    ensure_batched(product)
    for batch in product.batches:
        if (batch.batch_no or None) == (item.batch_no or None) and batch.expiry == item.expiry and D(batch.mrp) == mrp:
            return batch
    if D(item.qty) <= 0:
        return None  # returning goods we have no matching batch for: take from the first batch
    return Batch(product=product, batch_no=item.batch_no, expiry=item.expiry, mrp=mrp, qty=0,
                 sale_rate=D(item.sale_rate) or D(product.sale_rate), purchase_rate=item.unit_cost)


# --- Sales -----------------------------------------------------------------------


def save_sale(sale):
    if sale.status != "draft":
        raise BillError("This bill is already saved.")
    if not sale.items:
        raise BillError("Add at least one item.")
    if any(D(i.qty) == 0 for i in sale.items):
        raise BillError("Quantity can't be zero. Remove the line instead.")
    if sale.payment_mode == "credit" and sale.party is None:
        raise BillError("Choose a customer for a credit bill.")
    if sale.points_redeemed:
        _check_redeem(sale)

    store = Setting.get()
    sale.taxed = store.gst_on_sales
    for item in sale.items:
        if item.product:
            item.cost = item.product.cost_rate(sale.taxed, item.batch.purchase_rate if item.batch else None)
    sale.compute(Promotion.running())
    for item in sale.items:
        if item.product:
            change_stock(item.product, -D(item.qty), item.batch)
    sale.paid = _paid(sale)
    sale.points_earned = _points(sale, store)
    sale.number = Sale.next_value(Sale.number)
    sale.created_at = datetime.now()
    sale.status = "saved"


def _paid(doc):
    if doc.payment_mode != "credit":
        return doc.net
    return min(max(money(doc.paid), Decimal(0)), max(D(doc.net), Decimal(0)))


def _points(sale, store):
    if sale.party is None or D(store.loyalty_rate) <= 0:
        return 0
    return int(D(sale.net) * D(store.loyalty_rate) / 100)  # truncates toward zero; negative on returns


def _check_redeem(sale):
    store = Setting.get()
    points = sale.points_redeemed
    if sale.party is None or D(store.loyalty_rate) <= 0:
        raise BillError("Points can only be used by a customer, with loyalty switched on.")
    available = sale.party.points
    if points < 0 or points > available:
        raise BillError(f"{sale.customer_name} has {available} points.")
    if points < store.loyalty_min_redeem:
        raise BillError(f"At least {store.loyalty_min_redeem} points must be used at a time.")


def apply_redeem(sale, points):
    """Use loyalty points on a draft bill as a discount."""
    sale.points_redeemed = points
    if points:
        _check_redeem(sale)
    sale.redeem_amount = money(points * D(Setting.get().loyalty_value))


def cancel_sale(sale):
    if sale.status != "saved":
        raise BillError("Only saved bills can be cancelled.")
    if any(r.status == "saved" for r in sale.returns):
        raise BillError("Items of this bill were returned. Cancel the credit note first.")
    for item in sale.items:
        if item.product:
            change_stock(item.product, D(item.qty), item.batch)
    sale.status = "cancelled"
    log("cancel bill", f"{sale.title}, ₹{sale.net}, {sale.customer_name}")


def returnable_qty(item):
    returned = sum((-D(r.qty) for r in SaleItem.query.filter_by(source_item_id=item.id)
                    if r.sale.status == "saved"), Decimal(0))
    return max(D(item.qty) - returned, Decimal(0))


def create_return(sale, quantities, mode, user):
    """A credit note for goods brought back from a saved bill. quantities: {sale item id: qty}."""
    if sale.kind != "sale" or sale.status != "saved":
        raise BillError("Returns can only be made against a saved bill.")
    if mode == "credit" and sale.party is None:
        raise BillError("Refund to account needs a customer on the bill.")
    left = {item.id: returnable_qty(item) for item in sale.items}
    note = Sale(kind="return", return_of=sale, party=sale.party, taxed=sale.taxed, payment_mode=mode, user=user,
                status="draft")
    db.session.add(note)
    for item in sale.items:
        qty = D(quantities.get(item.id))
        if not qty:
            continue
        if qty < 0 or qty > left[item.id]:
            raise BillError(f"{item.name}: at most {left[item.id].normalize():f} can be returned.")
        share = qty / D(item.qty)
        note.items.append(SaleItem(
            product=item.product, batch=item.batch, source_item=item, name=item.name, qty=-qty, mrp=item.mrp,
            rate=item.rate, disc_pct=item.disc_pct, gst_rate=item.gst_rate, cost=item.cost,
            promo_amount=money(-D(item.promo_amount) * share), promo_note=item.promo_note,
        ))
    if not note.items:
        raise BillError("Enter the quantity being returned.")  # the caller rolls back
    note.compute()
    # Discounts given on the whole bill come back in proportion to what is returned.
    if D(sale.gross):
        note.discount = money(D(note.gross) * sale.total_discount / D(sale.gross))
        note.compute()
    for line in note.items:
        if line.product:
            change_stock(line.product, -D(line.qty), line.batch)
    note.paid = note.net if mode != "credit" else Decimal(0)
    note.points_earned = _points(note, Setting.get())
    note.return_number = Sale.next_value(Sale.return_number)
    note.created_at = datetime.now()
    note.status = "saved"
    log("return", f"{note.title} against {sale.title}, ₹{note.net}")
    return note


# --- Purchases -------------------------------------------------------------------


def save_purchase(purchase):
    if purchase.status not in ("draft", "order"):
        raise BillError("This purchase is already saved.")
    if purchase.party is None:
        raise BillError("Choose a supplier.")
    if not purchase.items:
        raise BillError("Add at least one item.")
    if any(D(i.qty) == 0 for i in purchase.items):
        raise BillError("Quantity can't be zero. Remove the line instead.")

    purchase.compute()
    for item in purchase.items:
        product = item.product
        item.batch = _purchase_batch(item)
        change_stock(product, D(item.qty) + D(item.free_qty), item.batch)
        if D(item.qty) > 0:  # returns don't change prices
            product.purchase_rate = item.unit_cost
            product.gst_rate = item.gst_rate
            if D(item.mrp) > 0:
                product.mrp = item.mrp
            if D(item.sale_rate) > 0:
                product.sale_rate = item.sale_rate
            if item.batch is not None:
                item.batch.purchase_rate = item.unit_cost
    purchase.paid = _paid(purchase)
    purchase.number = purchase.next_value(type(purchase).number)
    purchase.created_at = datetime.now()
    purchase.status = "saved"


def cancel_purchase(purchase):
    if purchase.status != "saved":
        raise BillError("Only saved purchases can be cancelled.")
    for item in purchase.items:
        change_stock(item.product, -(D(item.qty) + D(item.free_qty)), item.batch)
    purchase.status = "cancelled"
    log("cancel purchase", f"Purchase {purchase.number}, {purchase.supplier_name}, ₹{purchase.net}")
