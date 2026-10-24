from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()

CENT = Decimal("0.01")
RUPEE = Decimal("1")
PAYMENT_MODES = ("cash", "upi", "card", "credit")
# GST 2.0 slabs (from 22 Sep 2025): nil, 5% merit, 18% standard, 40% demerit,
# plus special rates for gold/silver and rough diamonds.
DEFAULT_TAX_SLABS = ((0, "GST Nil / Exempt"), (5, "GST 5%"), (18, "GST 18%"), (40, "GST 40%"),
                     (3, "GST 3% (gold, silver)"), (Decimal("0.25"), "GST 0.25% (rough diamonds)"))


def D(value):
    """Coerce DB values, form strings and numbers to Decimal."""
    if value is None or value == "":
        return Decimal(0)
    return value if isinstance(value, Decimal) else Decimal(str(value))


def money(value):
    return D(value).quantize(CENT, rounding=ROUND_HALF_UP)


class Setting(db.Model):
    """Store-wide settings; a single row with id 1, created at startup."""

    id = db.Column(db.Integer, primary_key=True)
    store_name = db.Column(db.String(120), nullable=False, default="My Mart")
    address = db.Column(db.Text)
    phone = db.Column(db.String(40))
    gstin = db.Column(db.String(20))
    # Off for composition dealers: sale prices carry no GST and costs include it.
    gst_on_sales = db.Column(db.Boolean, nullable=False, default=True)
    bill_footer = db.Column(db.String(200), default="Thank you! Visit again.")
    upi_id = db.Column(db.String(80))  # shown as a payment QR code on bills
    # Automatic backups, e.g. into a Google Drive for desktop folder.
    backup_dir = db.Column(db.String(500))
    backup_hours = db.Column(db.Integer, nullable=False, default=6)  # 0 = off
    backup_keep = db.Column(db.Integer, nullable=False, default=30)
    last_backup_at = db.Column(db.DateTime)
    last_backup_note = db.Column(db.String(300))

    @classmethod
    def get(cls):
        return db.session.get(cls, 1)


# Sections a user can be allowed into; admins can use everything.
PERMISSIONS = {
    "sales": "Billing (sales)",
    "purchases": "Purchases",
    "products": "Products & import/export",
    "parties": "Customers & suppliers",
    "stock": "Stock adjustment",
    "reports": "Reports",
    "settings": "Store settings & tax slabs",
}


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), nullable=False, unique=True)
    name = db.Column(db.String(80), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    permissions = db.Column(db.String(200), nullable=False, default="sales")  # comma-separated keys
    active = db.Column(db.Boolean, nullable=False, default=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def permission_set(self):
        return set(PERMISSIONS) if self.is_admin else set(filter(None, self.permissions.split(",")))

    def can(self, permission):
        return permission in self.permission_set


class TaxSlab(db.Model):
    """A GST rate offered when creating products and purchases. Intra-state
    sales split it equally into CGST and SGST."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), nullable=False)
    rate = db.Column(db.Numeric(5, 2), nullable=False, unique=True)
    active = db.Column(db.Boolean, nullable=False, default=True)

    @classmethod
    def choices(cls, include=None):
        """Active slabs, plus `include` (a product's current rate) if it is no longer active."""
        slabs = cls.query.order_by(cls.rate).all()
        return [s for s in slabs if s.active or (include is not None and D(s.rate) == D(include))]


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False, index=True)
    barcode = db.Column(db.String(64), unique=True, index=True)
    category = db.Column(db.String(80), index=True)
    brand = db.Column(db.String(80))
    hsn = db.Column(db.String(20))
    unit = db.Column(db.String(10), nullable=False, default="pcs")
    mrp = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    sale_rate = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # GST inclusive
    purchase_rate = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # per unit, before GST
    gst_rate = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    stock = db.Column(db.Numeric(12, 3), nullable=False, default=0)
    min_stock = db.Column(db.Numeric(12, 3), nullable=False, default=0)

    def cost_rate(self, taxed):
        """Unit cost used for profit: before GST if GST is claimed back, else including it."""
        rate = D(self.purchase_rate)
        return money(rate if taxed else rate * (1 + D(self.gst_rate) / 100))


class Party(db.Model):
    """A customer or a supplier."""

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(10), nullable=False, index=True)  # customer | supplier
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(40))
    address = db.Column(db.Text)
    gstin = db.Column(db.String(20))
    payments = db.relationship("PartyPayment", back_populates="party", order_by="PartyPayment.id.desc()")

    @property
    def documents(self):
        model = Sale if self.kind == "customer" else Purchase
        return model.query.filter_by(party_id=self.id, status="saved").order_by(model.id.desc())

    @property
    def balance(self):
        """Amount still owed: by the customer to us, or by us to the supplier."""
        model = Sale if self.kind == "customer" else Purchase
        billed = db.session.query(func.coalesce(func.sum(model.net - model.paid), 0)).filter(
            model.party_id == self.id, model.status == "saved"
        ).scalar()
        settled = db.session.query(func.coalesce(func.sum(PartyPayment.amount), 0)).filter(
            PartyPayment.party_id == self.id
        ).scalar()
        return money(D(billed) - D(settled))


class PartyPayment(db.Model):
    """Money received from a customer, or paid to a supplier, against their balance."""

    id = db.Column(db.Integer, primary_key=True)
    party_id = db.Column(db.Integer, db.ForeignKey("party.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    mode = db.Column(db.String(10), nullable=False, default="cash")
    note = db.Column(db.String(200))
    party = db.relationship("Party", back_populates="payments")


class _Document:
    """Shared behaviour of sale and purchase bills (draft -> saved -> cancelled)."""

    @property
    def cashier_name(self):
        return self.user.name if self.user else ""

    @property
    def due(self):
        return money(D(self.net) - D(self.paid))

    @property
    def total_qty(self):
        return sum((D(i.qty) for i in self.items), Decimal(0))

    def next_number(self):
        return (db.session.query(func.max(type(self).number)).scalar() or 0) + 1


class Sale(_Document, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    user = db.relationship("User")
    number = db.Column(db.Integer, unique=True)  # assigned when saved
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now, index=True)
    party_id = db.Column(db.Integer, db.ForeignKey("party.id"))
    payment_mode = db.Column(db.String(10), nullable=False, default="cash")
    status = db.Column(db.String(10), nullable=False, default="draft", index=True)
    taxed = db.Column(db.Boolean, nullable=False, default=True)
    discount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    paid = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    gross = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    tax = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    round_off = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    net = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    profit = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    party = db.relationship("Party")
    items = db.relationship("SaleItem", back_populates="sale", cascade="all, delete-orphan", order_by="SaleItem.id")

    @property
    def customer_name(self):
        return self.party.name if self.party else "Walk-in Customer"

    @property
    def savings(self):
        """What the customer saved against MRP, for the receipt."""
        below_mrp = sum(
            ((D(i.mrp) - D(i.rate)) * D(i.qty) for i in self.items if D(i.mrp) > D(i.rate)), Decimal(0)
        )
        return money(below_mrp + sum((i.line_discount for i in self.items), Decimal(0)) + D(self.discount))

    def add_product(self, product, qty=1):
        """Add a product, or increase its quantity if it is already on the bill."""
        for item in self.items:
            if item.product_id == product.id:
                item.qty = D(item.qty) + D(qty)
                return item
        item = SaleItem(
            product=product, name=product.name, qty=qty, mrp=product.mrp, rate=product.sale_rate,
            gst_rate=product.gst_rate, cost=product.cost_rate(self.taxed),
        )
        self.items.append(item)
        return item

    def compute(self):
        gross = sum((i.compute_amount() for i in self.items), Decimal(0))
        discount = min(money(self.discount), gross) if gross > 0 else Decimal(0)
        # A bill-level discount reduces every line's value proportionally.
        factor = (gross - discount) / gross if gross else Decimal(0)
        tax = profit = Decimal(0)
        for item in self.items:
            value = D(item.amount) * factor
            taxable = value / (1 + D(item.gst_rate) / 100) if self.taxed else value
            item.revenue = money(taxable)
            tax += value - taxable
            profit += item.revenue - money(D(item.cost) * D(item.qty))
        total = gross - discount
        self.gross, self.discount, self.tax, self.profit = gross, discount, money(tax), money(profit)
        self.net = total.quantize(RUPEE, rounding=ROUND_HALF_UP)
        self.round_off = self.net - total

    def tax_summary(self):
        """{gst rate: (taxable, cgst, sgst)} for receipts; SGST takes the rounding remainder."""
        totals = {}
        for item in self.items:
            rate = D(item.gst_rate)
            item_tax = D(item.revenue) * rate / 100 if self.taxed else Decimal(0)
            taxable, tax = totals.get(rate, (Decimal(0), Decimal(0)))
            totals[rate] = (taxable + D(item.revenue), tax + item_tax)
        summary = {}
        for rate, (taxable, tax) in sorted(totals.items()):
            tax = money(tax)
            cgst = money(tax / 2)
            summary[rate] = (money(taxable), cgst, tax - cgst)
        return summary


class SaleItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), index=True)
    name = db.Column(db.String(160), nullable=False)
    qty = db.Column(db.Numeric(12, 3), nullable=False, default=1)  # negative = returned
    mrp = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    rate = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    disc_pct = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    gst_rate = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    cost = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # unit cost when sold
    amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # after line discount, incl. GST
    revenue = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # after bill discount, excl. GST

    sale = db.relationship("Sale", back_populates="items")
    product = db.relationship("Product")

    @property
    def line_discount(self):
        return money(D(self.qty) * D(self.rate) - D(self.amount))

    def compute_amount(self):
        self.amount = money(D(self.qty) * D(self.rate) * (1 - D(self.disc_pct) / 100))
        return self.amount


class Purchase(_Document, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    user = db.relationship("User")
    number = db.Column(db.Integer, unique=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now, index=True)
    party_id = db.Column(db.Integer, db.ForeignKey("party.id"))
    bill_no = db.Column(db.String(40))
    bill_date = db.Column(db.Date, default=date.today)
    payment_mode = db.Column(db.String(10), nullable=False, default="credit")
    status = db.Column(db.String(10), nullable=False, default="draft", index=True)
    paid = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    gross = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # taxable value
    tax = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    round_off = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    net = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    party = db.relationship("Party")
    items = db.relationship(
        "PurchaseItem", back_populates="purchase", cascade="all, delete-orphan", order_by="PurchaseItem.id"
    )

    @property
    def supplier_name(self):
        return self.party.name if self.party else ""

    def add_product(self, product, qty=1):
        for item in self.items:
            if item.product_id == product.id:
                item.qty = D(item.qty) + D(qty)
                return item
        item = PurchaseItem(
            product=product, qty=qty, rate=product.purchase_rate, gst_rate=product.gst_rate,
            mrp=product.mrp, sale_rate=product.sale_rate,
        )
        self.items.append(item)
        return item

    def compute(self):
        for item in self.items:
            item.compute_amount()
        self.gross = sum((D(i.taxable) for i in self.items), Decimal(0))
        self.tax = sum((D(i.tax) for i in self.items), Decimal(0))
        total = self.gross + self.tax
        self.net = total.quantize(RUPEE, rounding=ROUND_HALF_UP)
        self.round_off = self.net - total


class PurchaseItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    purchase_id = db.Column(db.Integer, db.ForeignKey("purchase.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False, index=True)
    qty = db.Column(db.Numeric(12, 3), nullable=False, default=1)  # negative = returned to supplier
    free_qty = db.Column(db.Numeric(12, 3), nullable=False, default=0)
    rate = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # before GST
    disc_pct = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    gst_rate = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    mrp = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    sale_rate = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    taxable = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    tax = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    purchase = db.relationship("Purchase", back_populates="items")
    product = db.relationship("Product")

    def compute_amount(self):
        self.taxable = money(D(self.qty) * D(self.rate) * (1 - D(self.disc_pct) / 100))
        self.tax = money(D(self.taxable) * D(self.gst_rate) / 100)
        self.amount = self.taxable + self.tax

    @property
    def unit_cost(self):
        """Cost per unit before GST, spreading the price over free units too."""
        units = D(self.qty) + D(self.free_qty)
        return money(D(self.taxable) / units) if units else D(self.rate)


class StockAdjustment(db.Model):
    """A physical count, or stock written off as damaged/expired."""

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now, index=True)
    system_qty = db.Column(db.Numeric(12, 3), nullable=False)
    actual_qty = db.Column(db.Numeric(12, 3), nullable=False)
    reason = db.Column(db.String(20), nullable=False, default="count")
    note = db.Column(db.String(200))
    product = db.relationship("Product")

    @property
    def difference(self):
        return D(self.actual_qty) - D(self.system_qty)
