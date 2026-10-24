from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

CENT = Decimal("0.01")
INVOICE_STATUSES = ("draft", "sent", "paid", "void")


def _round(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120))
    phone = db.Column(db.String(40))
    address = db.Column(db.Text)
    invoices = db.relationship("Invoice", back_populates="customer")


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    barcode = db.Column(db.String(64), unique=True, index=True)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)


class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    issue_date = db.Column(db.Date, nullable=False, default=date.today)
    due_date = db.Column(db.Date, nullable=False, default=lambda: date.today() + timedelta(days=30))
    tax_rate = db.Column(db.Numeric(5, 2), nullable=False, default=0)  # percent
    status = db.Column(db.String(10), nullable=False, default="draft")
    notes = db.Column(db.Text)

    customer = db.relationship("Customer", back_populates="invoices")
    items = db.relationship(
        "InvoiceItem", back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceItem.id"
    )

    @property
    def number(self):
        return f"INV-{self.id:05d}"

    @property
    def subtotal(self):
        return _round(sum((item.amount for item in self.items), Decimal(0)))

    @property
    def tax(self):
        return _round(self.subtotal * Decimal(self.tax_rate) / 100)

    @property
    def total(self):
        return self.subtotal + self.tax

    def add_product(self, product, quantity=1):
        """Add a product line, or bump its quantity if it is already on the invoice."""
        for item in self.items:
            if item.product_id == product.id:
                item.quantity = Decimal(item.quantity) + quantity
                return item
        item = InvoiceItem(
            product=product, description=product.name, quantity=quantity, unit_price=product.unit_price
        )
        self.items.append(item)
        return item


class InvoiceItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoice.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"))
    description = db.Column(db.String(200), nullable=False)
    quantity = db.Column(db.Numeric(10, 2), nullable=False, default=1)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    invoice = db.relationship("Invoice", back_populates="items")
    product = db.relationship("Product")

    @property
    def amount(self):
        return _round(Decimal(self.quantity) * Decimal(self.unit_price))
