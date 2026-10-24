import re
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from ..exporting import cell_text, export_response, read_rows
from ..models import D, Product, Purchase, PurchaseItem, Sale, SaleItem, TaxSlab, db
from ..services import parse_decimal, product_query

bp = Blueprint("products", __name__, url_prefix="/products")
PAGE_SIZE = 50

# Export columns; import accepts these headers and the aliases below (SwilERP and common Excel layouts).
FIELDS = [
    ("name", "Name", "text"), ("barcode", "Barcode", "text"), ("category", "Category", "text"),
    ("brand", "Brand", "text"), ("hsn", "HSN", "text"), ("unit", "Unit", "text"), ("mrp", "MRP", "money"),
    ("sale_rate", "Sale Rate", "money"), ("purchase_rate", "Purchase Rate", "money"),
    ("gst_rate", "GST %", "money"), ("stock", "Stock", "qty"), ("min_stock", "Min Stock", "qty"),
]
ALIASES = {
    "name": ["name", "nametodisplay", "productname", "itemname", "product", "item", "description"],
    "barcode": ["barcode", "ean", "eancode", "upc", "code", "itemcode"],
    "category": ["category", "group", "productgroup", "department"],
    "brand": ["brand", "company", "manufacturer", "marketingcompany", "groupname"],
    "hsn": ["hsn", "hsncode", "hsnsac"],
    "unit": ["unit", "uom"],
    "mrp": ["mrp"],
    "sale_rate": ["salerate", "saleprice", "sellingprice", "rate", "price"],
    "purchase_rate": ["purchaserate", "purchaseprice", "costrate", "cost", "traderate"],
    "gst_rate": ["gstrate", "gst", "gstpercent", "taxrate", "tax", "igst"],
    "stock": ["stock", "currqty", "currentstock", "closingstock", "qty", "quantity", "openingstock"],
    "min_stock": ["minstock", "minimumstock", "reorderlevel", "minqty"],
}
NUMERIC = {"mrp", "sale_rate", "purchase_rate", "gst_rate", "stock", "min_stock"}


@bp.route("/")
def index():
    q = request.args.get("q", "").strip()
    page = max(request.args.get("page", 1, type=int), 1)
    query = product_query(q)
    count = query.count()
    return render_template(
        "products/list.html",
        products=query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all(),
        q=q, page=page, count=count, pages=max((count - 1) // PAGE_SIZE + 1, 1),
    )


@bp.route("/new", methods=["GET", "POST"])
@bp.route("/<int:product_id>", methods=["GET", "POST"])
def edit(product_id=None):
    product = db.get_or_404(Product, product_id) if product_id else Product(
        barcode=request.args.get("barcode") or None, unit="pcs"
    )
    if request.method == "POST":
        error = _fill(product, request.form)
        if not error:
            db.session.add(product)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                error = f"Barcode {product.barcode} is already used by another product."
        if error:
            flash(error, "error")
        else:
            flash(f"{product.name} saved.", "success")
            back = _add_to_bill(product, request.args.get("add_to", ""))
            if back:
                return back
            if request.form.get("next") == "new":
                return redirect(url_for("products.edit"))
            return redirect(url_for("products.index", q=product.barcode or product.name))
    categories = [c for (c,) in db.session.query(Product.category).distinct() if c]
    return render_template("products/form.html", product=product, slabs=TaxSlab.choices(product.gst_rate),
                           categories=sorted(categories))


def _add_to_bill(product, target):
    """add_to=sale:<id> or purchase:<id>: put a newly created product on that draft bill."""
    kind, _, doc_id = target.partition(":")
    model = {"sale": Sale, "purchase": Purchase}.get(kind)
    doc = db.session.get(model, int(doc_id)) if model and doc_id.isdigit() else None
    if doc is None or doc.status != "draft":
        return None
    item = doc.add_product(product)
    doc.compute()
    db.session.commit()
    if kind == "sale":
        return redirect(url_for("sales.bill", sale_id=doc.id, last=item.id))
    return redirect(url_for("purchases.bill", purchase_id=doc.id, last=item.id))


def _fill(product, form):
    product.name = form.get("name", "").strip()
    product.barcode = form.get("barcode", "").strip() or None
    for field in ("category", "brand", "hsn"):
        setattr(product, field, form.get(field, "").strip() or None)
    product.unit = form.get("unit", "pcs").strip() or "pcs"
    if not product.name:
        return "Name is required."
    for field in NUMERIC:
        value = parse_decimal(form.get(field, ""), D(0))
        if value is None or (field != "stock" and value < 0):
            return f"{field.replace('_', ' ').capitalize()} must be a valid number."
        setattr(product, field, value)
    if D(product.mrp) and D(product.sale_rate) > D(product.mrp):
        return "Sale rate can't be more than MRP."
    return None


@bp.route("/<int:product_id>/delete", methods=["POST"])
def delete(product_id):
    product = db.get_or_404(Product, product_id)
    used = SaleItem.query.filter_by(product_id=product.id).first() or PurchaseItem.query.filter_by(
        product_id=product.id
    ).first()
    if used:
        flash(f"{product.name} is on saved bills, so it can't be deleted.", "error")
        return redirect(url_for("products.edit", product_id=product.id))
    db.session.delete(product)
    db.session.commit()
    flash(f"{product.name} deleted.", "success")
    return redirect(url_for("products.index"))


@bp.route("/export")
def export():
    fmt = request.args.get("format", "xlsx")
    q = request.args.get("q", "").strip()
    columns = [(label, kind) for _, label, kind in FIELDS]
    rows = [[getattr(p, f) if getattr(p, f) is not None else "" for f, _, _ in FIELDS]
            for p in product_query(q)]
    return export_response("Products", columns, rows, fmt=fmt if fmt in ("xlsx", "csv") else "xlsx")


def _key(header):
    return re.sub(r"[^a-z0-9]", "", str(header).lower())


@bp.route("/import", methods=["GET", "POST"])
def import_():
    if request.method == "GET":
        return render_template("products/import.html", fields=FIELDS, aliases=ALIASES)
    upload = request.files.get("file")
    update_stock = request.form.get("update_stock") == "1"
    if not upload or not upload.filename:
        flash("Choose a file to import.", "error")
        return redirect(url_for("products.import_"))
    try:
        rows = list(read_rows(upload))
    except Exception as e:  # unreadable/corrupt spreadsheet
        flash(f"Couldn't read the file: {e}", "error")
        return redirect(url_for("products.import_"))
    if not rows:
        flash("The file has no rows.", "error")
        return redirect(url_for("products.import_"))

    headers = {_key(h): h for h in rows[0].keys() if h}
    mapping = {}
    for field, aliases in ALIASES.items():
        source = next((headers[a] for a in aliases if a in headers), None)
        if source and source not in mapping.values():
            mapping[field] = source
    if "name" not in mapping:
        flash("No product name column found. Name a column 'Name'.", "error")
        return redirect(url_for("products.import_"))

    by_barcode = {p.barcode: p for p in Product.query.filter(Product.barcode.isnot(None))}
    by_name = {p.name.lower(): p for p in Product.query}
    created = updated = 0
    problems = []
    for line, row in enumerate(rows, start=2):
        values = {field: cell_text(row.get(source)) for field, source in mapping.items()}
        name, barcode = values.get("name", ""), values.get("barcode") or None
        if not name:
            continue
        product = (by_barcode.get(barcode) if barcode else None) or by_name.get(name.lower())
        is_new = product is None
        if is_new:
            product = Product(name=name, unit="pcs")
            db.session.add(product)
        product.name = name
        if barcode:
            other = by_barcode.get(barcode)
            if other is not None and other is not product:
                problems.append(f"Row {line}: barcode {barcode} already belongs to {other.name}")
                barcode = None
            else:
                product.barcode = barcode
                by_barcode[barcode] = product
        for field in ("category", "brand", "hsn", "unit"):
            if values.get(field):
                setattr(product, field, values[field])
        for field in NUMERIC:
            if field not in values or values[field] == "" or (field == "stock" and not is_new and not update_stock):
                continue
            number = parse_decimal(values[field].replace(",", "").rstrip("%"))
            if number is None:
                problems.append(f"Row {line}: {field} '{values[field]}' is not a number")
                continue
            setattr(product, field, number)
        if product.sale_rate in (None, Decimal(0)) and product.mrp:
            product.sale_rate = product.mrp
        by_name[name.lower()] = product
        created += is_new
        updated += not is_new
    db.session.commit()
    flash(f"Import done: {created} new, {updated} updated.", "success")
    for problem in problems[:20]:
        flash(problem, "error")
    if len(problems) > 20:
        flash(f"…and {len(problems) - 20} more problems.", "error")
    return redirect(url_for("products.index"))
