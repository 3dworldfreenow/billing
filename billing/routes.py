from datetime import date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from .models import INVOICE_STATUSES, Customer, Invoice, InvoiceItem, Product, db

bp = Blueprint("billing", __name__)


def _decimal(value, default="0"):
    try:
        return Decimal(value.strip() or default)
    except (InvalidOperation, AttributeError):
        return None


def _date(value, default):
    try:
        return date.fromisoformat(value) if value else default
    except ValueError:
        return None


@bp.route("/")
def index():
    return redirect(url_for("billing.invoices"))


# --- Customers ---------------------------------------------------------------


@bp.route("/customers")
def customers():
    return render_template("customers/list.html", customers=Customer.query.order_by(Customer.name).all())


@bp.route("/customers/new", methods=["GET", "POST"])
@bp.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
def customer_form(customer_id=None):
    customer = db.get_or_404(Customer, customer_id) if customer_id else Customer()
    if request.method == "POST":
        for field in ("name", "email", "phone", "address"):
            setattr(customer, field, request.form.get(field, "").strip() or None)
        if not customer.name:
            flash("Name is required.", "error")
        else:
            db.session.add(customer)
            db.session.commit()
            flash("Customer saved.", "success")
            return redirect(url_for("billing.customers"))
    return render_template("customers/form.html", customer=customer)


# --- Products ----------------------------------------------------------------


@bp.route("/products")
def products():
    return render_template("products/list.html", products=Product.query.order_by(Product.name).all())


@bp.route("/products/new", methods=["GET", "POST"])
@bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
def product_form(product_id=None):
    product = db.get_or_404(Product, product_id) if product_id else Product()
    if request.method == "POST":
        product.name = request.form.get("name", "").strip()
        product.barcode = request.form.get("barcode", "").strip() or None
        price = _decimal(request.form.get("unit_price", ""))
        if not product.name:
            flash("Name is required.", "error")
        elif price is None or price < 0:
            flash("Price must be a non-negative number.", "error")
        else:
            product.unit_price = price
            db.session.add(product)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash(f"Barcode {product.barcode} is already used by another product.", "error")
            else:
                flash("Product saved.", "success")
                return redirect(url_for("billing.products"))
    return render_template("products/form.html", product=product)


@bp.route("/api/products/barcode/<code>")
def product_by_barcode(code):
    product = Product.query.filter_by(barcode=code.strip()).first()
    if product is None:
        return jsonify(error="not found"), 404
    return jsonify(id=product.id, name=product.name, barcode=product.barcode, unit_price=str(product.unit_price))


# --- Invoices ----------------------------------------------------------------


@bp.route("/invoices")
def invoices():
    return render_template("invoices/list.html", invoices=Invoice.query.order_by(Invoice.id.desc()).all())


@bp.route("/invoices/new", methods=["GET", "POST"])
def invoice_new():
    customers = Customer.query.order_by(Customer.name).all()
    if request.method == "POST":
        customer = db.session.get(Customer, request.form.get("customer_id", type=int) or 0)
        issue = _date(request.form.get("issue_date"), date.today())
        due = _date(request.form.get("due_date"), False)
        tax_rate = _decimal(request.form.get("tax_rate", ""))
        if customer is None:
            flash("Choose a customer.", "error")
        elif issue is None or due is None:
            flash("Dates must be valid.", "error")
        elif tax_rate is None or not 0 <= tax_rate <= 100:
            flash("Tax rate must be between 0 and 100.", "error")
        else:
            invoice = Invoice(
                customer=customer,
                issue_date=issue,
                tax_rate=tax_rate,
                notes=request.form.get("notes", "").strip() or None,
            )
            if due:
                invoice.due_date = due
            db.session.add(invoice)
            db.session.commit()
            return redirect(url_for("billing.invoice_detail", invoice_id=invoice.id))
    if not customers:
        flash("Add a customer before creating an invoice.", "error")
    return render_template("invoices/new.html", customers=customers, today=date.today())


def _editable_invoice(invoice_id):
    invoice = db.get_or_404(Invoice, invoice_id)
    if invoice.status != "draft":
        abort(409, description="Only draft invoices can be edited.")
    return invoice


@bp.route("/invoices/<int:invoice_id>")
def invoice_detail(invoice_id):
    invoice = db.get_or_404(Invoice, invoice_id)
    return render_template("invoices/detail.html", invoice=invoice, statuses=INVOICE_STATUSES)


@bp.route("/invoices/<int:invoice_id>/scan", methods=["POST"])
def invoice_scan(invoice_id):
    invoice = _editable_invoice(invoice_id)
    code = request.form.get("barcode", "").strip()
    product = Product.query.filter_by(barcode=code).first() if code else None
    if product is None:
        flash(f"No product with barcode {code!r}." if code else "Scan or type a barcode.", "error")
    else:
        item = invoice.add_product(product)
        db.session.commit()
        flash(f"Added {product.name} (qty {item.quantity.normalize():f}).", "success")
    return redirect(url_for("billing.invoice_detail", invoice_id=invoice.id))


@bp.route("/invoices/<int:invoice_id>/items", methods=["POST"])
def invoice_add_item(invoice_id):
    invoice = _editable_invoice(invoice_id)
    description = request.form.get("description", "").strip()
    quantity = _decimal(request.form.get("quantity", ""), "1")
    unit_price = _decimal(request.form.get("unit_price", ""))
    if not description:
        flash("Description is required.", "error")
    elif quantity is None or quantity <= 0 or unit_price is None or unit_price < 0:
        flash("Quantity must be positive and price non-negative.", "error")
    else:
        invoice.items.append(InvoiceItem(description=description, quantity=quantity, unit_price=unit_price))
        db.session.commit()
    return redirect(url_for("billing.invoice_detail", invoice_id=invoice.id))


@bp.route("/invoices/<int:invoice_id>/items/<int:item_id>/delete", methods=["POST"])
def invoice_delete_item(invoice_id, item_id):
    invoice = _editable_invoice(invoice_id)
    item = db.get_or_404(InvoiceItem, item_id)
    if item.invoice_id != invoice.id:
        abort(404)
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for("billing.invoice_detail", invoice_id=invoice.id))


@bp.route("/invoices/<int:invoice_id>/status", methods=["POST"])
def invoice_status(invoice_id):
    invoice = db.get_or_404(Invoice, invoice_id)
    status = request.form.get("status")
    if status not in INVOICE_STATUSES:
        abort(400)
    invoice.status = status
    db.session.commit()
    return redirect(url_for("billing.invoice_detail", invoice_id=invoice.id))
