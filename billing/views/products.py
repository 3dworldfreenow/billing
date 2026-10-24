from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from sqlalchemy.exc import IntegrityError

from ..exporting import cell_text, export_response, map_columns, read_rows
from ..models import D, Product, Promotion, Purchase, PurchaseItem, Sale, SaleItem, TaxSlab, db
from ..services import BillError, find_products, generate_barcode, log, parse_decimal, product_query

bp = Blueprint("products", __name__, url_prefix="/products")
PAGE_SIZE = 50

# Export columns; import accepts these headers and the aliases below (SwilERP and common Excel layouts).
FIELDS = [
    ("name", "Name", "text"), ("barcode", "Barcode", "text"), ("plu", "Scale PLU", "text"),
    ("category", "Category", "text"), ("brand", "Brand", "text"), ("hsn", "HSN", "text"), ("unit", "Unit", "text"),
    ("mrp", "MRP", "money"), ("sale_rate", "Sale Rate", "money"), ("wholesale_rate", "Wholesale Rate", "money"),
    ("purchase_rate", "Purchase Rate", "money"), ("gst_rate", "GST %", "money"), ("stock", "Stock", "qty"),
    ("min_stock", "Min Stock", "qty"),
]
ALIASES = {
    "name": ["name", "nametodisplay", "productname", "itemname", "product", "item", "description"],
    "barcode": ["barcode", "ean", "eancode", "upc", "code", "itemcode"],
    "plu": ["scaleplu", "plu", "plucode", "scalecode"],
    "category": ["category", "group", "productgroup", "department"],
    "brand": ["brand", "company", "manufacturer", "marketingcompany", "groupname"],
    "hsn": ["hsn", "hsncode", "hsnsac"],
    "unit": ["unit", "uom"],
    "mrp": ["mrp"],
    "sale_rate": ["salerate", "saleprice", "sellingprice", "rate", "price"],
    "wholesale_rate": ["wholesalerate", "wholesaleprice", "wsrate"],
    "purchase_rate": ["purchaserate", "purchaseprice", "costrate", "cost", "traderate"],
    "gst_rate": ["gstrate", "gst", "gstpercent", "taxrate", "tax", "igst"],
    "stock": ["stock", "currqty", "currentstock", "closingstock", "qty", "quantity", "openingstock"],
    "min_stock": ["minstock", "minimumstock", "reorderlevel", "minqty"],
}
NUMERIC = {"mrp", "sale_rate", "wholesale_rate", "purchase_rate", "gst_rate", "stock", "min_stock"}
PRICES = ("mrp", "sale_rate", "wholesale_rate", "purchase_rate")


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
        before = {f: D(getattr(product, f)) for f in PRICES + ("stock",)} if product.id else None
        error = _fill(product, request.form)
        if not error:
            db.session.add(product)
            if before:
                changes = [f"{f.replace('_', ' ')} {before[f].normalize():f} → {D(getattr(product, f)).normalize():f}"
                           for f in before if before[f] != D(getattr(product, f))]
                if changes:
                    log("product edit", f"{product.name}: " + ", ".join(changes))
            try:
                db.session.flush()  # a new product needs its id for the photo's file name
                error = _photo(product, request)
                if error:
                    db.session.rollback()
                else:
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
    """add_to=sale:<id> or purchase:<id>: put a newly created product on that unsaved bill."""
    kind, _, doc_id = target.partition(":")
    model = {"sale": Sale, "purchase": Purchase}.get(kind)
    doc = db.session.get(model, int(doc_id)) if model and doc_id.isdigit() else None
    if doc is None or doc.status not in ("draft", "order"):
        return None
    item = doc.add_product(product)
    if kind == "sale":
        doc.compute(Promotion.running())
    else:
        doc.compute()
    db.session.commit()
    if kind == "sale":
        return redirect(url_for("sales.bill", sale_id=doc.id, last=item.id))
    return redirect(url_for("purchases.bill", purchase_id=doc.id, last=item.id))


def _photo(product, req):
    from ..images import ImageError, remove_product_image, save_product_image

    upload = req.files.get("image")
    try:
        if upload and upload.filename:
            save_product_image(upload, product)
        elif req.form.get("remove_image") == "1":
            remove_product_image(product)
    except ImageError as e:
        return str(e)
    return None


def _fill(product, form):
    product.name = form.get("name", "").strip()
    product.barcode = form.get("barcode", "").strip() or None
    for field in ("category", "brand", "hsn", "plu"):
        setattr(product, field, form.get(field, "").strip() or None)
    product.unit = form.get("unit", "pcs").strip() or "pcs"
    product.online = form.get("online") == "1"
    if not product.name:
        return "Name is required."
    for field in NUMERIC:
        if field == "stock" and product.batches:
            continue  # batched stock changes through purchases and stock adjustment
        value = parse_decimal(form.get(field, ""), D(0))
        if value is None or (field != "stock" and value < 0):
            return f"{field.replace('_', ' ').capitalize()} must be a valid number."
        setattr(product, field, value)
    if D(product.mrp) and D(product.sale_rate) > D(product.mrp):
        return "Sale rate can't be more than MRP."
    if product.plu and not product.plu.isdigit():
        return "Scale PLU must be a number."
    return None


@bp.route("/<int:product_id>/barcode", methods=["POST"])
def make_barcode(product_id):
    product = db.get_or_404(Product, product_id)
    if product.barcode:
        flash(f"{product.name} already has barcode {product.barcode}.", "error")
    else:
        try:
            code = generate_barcode(product)
            db.session.commit()
            flash(f"Barcode {code} created for {product.name}.", "success")
        except BillError as e:
            flash(str(e), "error")
    return redirect(url_for("products.edit", product_id=product.id))


@bp.route("/<int:product_id>/delete", methods=["POST"])
def delete(product_id):
    product = db.get_or_404(Product, product_id)
    used = SaleItem.query.filter_by(product_id=product.id).first() or PurchaseItem.query.filter_by(
        product_id=product.id
    ).first()
    if used:
        flash(f"{product.name} is on saved bills, so it can't be deleted.", "error")
        return redirect(url_for("products.edit", product_id=product.id))
    for batch in product.batches:
        db.session.delete(batch)
    db.session.delete(product)
    log("delete product", product.name)
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
    mapping = map_columns(rows, ALIASES)
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
            else:
                product.barcode = barcode
                by_barcode[barcode] = product
        for field in ("category", "brand", "hsn", "unit", "plu"):
            if values.get(field):
                setattr(product, field, values[field])
        for field in NUMERIC:
            if field not in values or values[field] == "" or (field == "stock" and not is_new and not update_stock):
                continue
            if field == "stock" and product.batches:
                problems.append(f"Row {line}: {name} has batches, so its stock wasn't changed")
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
    log("import products", f"{created} new, {updated} updated from {upload.filename}")
    db.session.commit()
    flash(f"Import done: {created} new, {updated} updated.", "success")
    for problem in problems[:20]:
        flash(problem, "error")
    if len(problems) > 20:
        flash(f"…and {len(problems) - 20} more problems.", "error")
    return redirect(url_for("products.index"))


# --- Price labels -------------------------------------------------------------------
# The label queue lives in the user's session: [[product id, copies], ...]

LAYOUTS = {
    "a4-65": "A4 sheet, 65 labels (38 × 21 mm)",
    "a4-24": "A4 sheet, 24 labels (64 × 34 mm)",
    "roll-50x25": "Label roll printer, 50 × 25 mm",
}


def _queue():
    return session.setdefault("labels", [])


def _queue_add(product_id, copies):
    queue = _queue()
    for row in queue:
        if row[0] == product_id:
            row[1] += copies
            break
    else:
        queue.append([product_id, copies])
    session.modified = True


@bp.route("/labels", methods=["GET", "POST"])
def labels():
    if request.method == "POST":
        copies = max(request.form.get("copies", 1, type=int), 1)
        product = db.session.get(Product, request.form.get("product_id", type=int) or 0)
        if product is None:
            matches = find_products(request.form.get("code", ""))
            product = matches[0] if len(matches) == 1 else None
        if product is None:
            flash("Pick one product (scan its barcode or choose from the suggestions).", "error")
        else:
            _queue_add(product.id, copies)
        return redirect(url_for("products.labels"))
    products = {p.id: p for p in Product.query.filter(Product.id.in_([r[0] for r in _queue()]))}
    rows = [(products[pid], copies) for pid, copies in _queue() if pid in products]
    return render_template("products/labels.html", rows=rows, layouts=LAYOUTS,
                           missing=sum(1 for p, _ in rows if not p.barcode))


@bp.route("/labels/update", methods=["POST"])
def labels_update():
    action = request.form.get("action", "")
    if action == "clear":
        session["labels"] = []
    elif action == "barcodes":  # give every queued product without a barcode an in-store one
        for pid, _ in _queue():
            product = db.session.get(Product, pid)
            if product and not product.barcode:
                generate_barcode(product)
                db.session.flush()
        db.session.commit()
    else:
        queue = []
        for pid, _ in _queue():
            copies = request.form.get(f"copies_{pid}", 0, type=int)
            if copies > 0 and action != f"remove:{pid}":
                queue.append([pid, copies])
        session["labels"] = queue
    return redirect(url_for("products.labels"))


@bp.route("/labels/from-purchase/<int:purchase_id>", methods=["POST"])
def labels_from_purchase(purchase_id):
    purchase = db.get_or_404(Purchase, purchase_id)
    for item in purchase.items:
        copies = int(D(item.qty) + D(item.free_qty))
        if copies > 0:
            _queue_add(item.product_id, copies)
    flash(f"Labels for purchase {purchase.number or purchase.order_number} added.", "success")
    return redirect(url_for("products.labels"))


@bp.route("/labels/print")
def labels_print():
    from ..labels import barcode_svg

    layout = request.args.get("layout", "a4-65")
    layout = layout if layout in LAYOUTS else "a4-65"
    products = {p.id: p for p in Product.query.filter(Product.id.in_([r[0] for r in _queue()]))}
    labels = []
    bar_height = {"a4-65": 4.5, "a4-24": 9, "roll-50x25": 6}[layout]
    for pid, copies in _queue():
        product = products.get(pid)
        if product and product.barcode:
            svg = barcode_svg(product.barcode, bar_height)
            labels += [(product, svg)] * min(copies, 1000)
    show_price = request.args.get("price", "1") == "1"
    return render_template("products/labels_print.html", labels=labels, layout=layout, show_price=show_price)
