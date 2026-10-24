"""Posting rules: what saving or cancelling a bill does to numbers, stock and prices."""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import case, or_

from .models import D, Product, Setting, db, money


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


def save_sale(sale):
    if sale.status != "draft":
        raise BillError("This bill is already saved.")
    if not sale.items:
        raise BillError("Add at least one item.")
    if any(D(i.qty) == 0 for i in sale.items):
        raise BillError("Quantity can't be zero. Remove the line instead.")
    if sale.payment_mode == "credit" and sale.party is None:
        raise BillError("Choose a customer for a credit bill.")

    sale.taxed = Setting.get().gst_on_sales
    for item in sale.items:
        if item.product:
            item.cost = item.product.cost_rate(sale.taxed)
            item.product.stock = D(item.product.stock) - D(item.qty)
    sale.compute()
    if sale.payment_mode == "credit":
        sale.paid = min(max(money(sale.paid), Decimal(0)), max(D(sale.net), Decimal(0)))
    else:
        sale.paid = sale.net
    sale.number = sale.next_number()
    sale.created_at = datetime.now()
    sale.status = "saved"


def cancel_sale(sale):
    if sale.status != "saved":
        raise BillError("Only saved bills can be cancelled.")
    for item in sale.items:
        if item.product:
            item.product.stock = D(item.product.stock) + D(item.qty)
    sale.status = "cancelled"


def save_purchase(purchase):
    if purchase.status != "draft":
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
        product.stock = D(product.stock) + D(item.qty) + D(item.free_qty)
        if D(item.qty) > 0:  # returns don't change prices
            product.purchase_rate = item.unit_cost
            product.gst_rate = item.gst_rate
            if D(item.mrp) > 0:
                product.mrp = item.mrp
            if D(item.sale_rate) > 0:
                product.sale_rate = item.sale_rate
    purchase.paid = purchase.net if purchase.payment_mode != "credit" else min(
        max(money(purchase.paid), Decimal(0)), max(D(purchase.net), Decimal(0))
    )
    purchase.number = purchase.next_number()
    purchase.created_at = datetime.now()
    purchase.status = "saved"


def cancel_purchase(purchase):
    if purchase.status != "saved":
        raise BillError("Only saved purchases can be cancelled.")
    for item in purchase.items:
        item.product.stock = D(item.product.stock) - D(item.qty) - D(item.free_qty)
    purchase.status = "cancelled"
