import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()

CENT = Decimal("0.01")
RUPEE = Decimal("1")
PAYMENT_MODES = ("cash", "upi", "card", "credit")
EXPENSE_MODES = ("cash", "upi", "card", "bank")
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


def slugify(text):
    """Product name -> web address part: "Amul Butter 500g" -> "amul-butter-500g"."""
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:80].rstrip("-") or "item"

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
    # Loyalty: points earned per ₹100 of bill value (0 = off), and what one point is worth.
    loyalty_rate = db.Column(db.Numeric(6, 2), nullable=False, default=0)
    loyalty_value = db.Column(db.Numeric(6, 2), nullable=False, default=1)
    loyalty_min_redeem = db.Column(db.Integer, nullable=False, default=0)
    # Weighing-scale barcodes (EAN-13): prefix + item code (PLU) + weight/price + check digit.
    scale_prefix = db.Column(db.String(3))  # empty = off
    scale_item_digits = db.Column(db.Integer, nullable=False, default=5)
    scale_value = db.Column(db.String(10), nullable=False, default="weight")  # weight (grams) | price (paise)
    # Receipt printing: browser print dialog, or straight to a thermal printer.
    printer_mode = db.Column(db.String(10), nullable=False, default="browser")  # browser | network | windows
    printer_address = db.Column(db.String(120))  # 192.168.1.50:9100, or a Windows printer name
    printer_width = db.Column(db.Integer, nullable=False, default=48)  # characters: 48 for 80mm, 32 for 58mm
    auto_print = db.Column(db.Boolean, nullable=False, default=True)
    open_drawer = db.Column(db.Boolean, nullable=False, default=True)
    # Unused: from the old Google Drive direct upload (kept so existing databases stay valid).
    drive_client_id = db.Column(db.String(200))
    drive_client_secret = db.Column(db.String(200))
    drive_refresh_token = db.Column(db.String(500))
    drive_folder_id = db.Column(db.String(100))
    drive_auto = db.Column(db.Boolean, nullable=False, default=False)
    last_drive_at = db.Column(db.DateTime)
    last_drive_note = db.Column(db.String(300))
    # Online shop for approved customers (off = only the admin can preview it).
    shop_enabled = db.Column(db.Boolean, nullable=False, default=False)
    shop_note = db.Column(db.String(300))  # shown on the shop, e.g. delivery timings
    shop_min_order = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    # SEO mode: anyone (and Google) can browse and fill a cart; only approved customers order.
    shop_public = db.Column(db.Boolean, nullable=False, default=False)
    shop_seo_title = db.Column(db.String(80))
    shop_seo_description = db.Column(db.String(200))
    # Google Gemini, to help write product descriptions and SEO text.
    gemini_api_key = db.Column(db.String(200))
    gemini_model = db.Column(db.String(80))

    @classmethod
    def get(cls):
        return db.session.get(cls, 1)

    @property
    def state_code(self):
        return (self.gstin or "")[:2]


# Sections a user can be allowed into; admins can use everything.
PERMISSIONS = {
    "sales": "Billing (sales)",
    "rates": "Change rates on a bill",
    "cancel": "Cancel bills & make returns",
    "cash": "Cash register & expenses",
    "purchases": "Purchases & orders",
    "products": "Products, labels & import/export",
    "parties": "Customers & suppliers",
    "orders": "Online orders",
    "stock": "Stock adjustment",
    "offers": "Offers & loyalty",
    "reports": "Reports",
    "settings": "Store settings",
}


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), nullable=False, unique=True)
    name = db.Column(db.String(80), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    permissions = db.Column(db.String(200), nullable=False, default="sales")  # comma-separated keys
    active = db.Column(db.Boolean, nullable=False, default=True)
    max_discount = db.Column(db.Numeric(5, 2), nullable=False, default=100)  # % of a line or bill

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def permission_set(self):
        return set(PERMISSIONS) if self.is_admin else set(filter(None, self.permissions.split(",")))

    def can(self, permission):
        return permission in self.permission_set

    @property
    def discount_limit(self):
        return Decimal(100) if self.is_admin else D(self.max_discount)


class AuditLog(db.Model):
    """Who did what: cancellations, returns, price and stock changes, settings and users."""

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    action = db.Column(db.String(40), nullable=False, index=True)
    detail = db.Column(db.String(500))
    user = db.relationship("User")


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
    plu = db.Column(db.String(10), index=True)  # item code programmed into the weighing scale
    category = db.Column(db.String(80), index=True)
    brand = db.Column(db.String(80))
    hsn = db.Column(db.String(20))
    unit = db.Column(db.String(10), nullable=False, default="pcs")
    mrp = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    sale_rate = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # GST inclusive
    wholesale_rate = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # 0 = same as sale rate
    purchase_rate = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # per unit, before GST
    gst_rate = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    stock = db.Column(db.Numeric(12, 3), nullable=False, default=0)  # = sum of batch qty when batched
    min_stock = db.Column(db.Numeric(12, 3), nullable=False, default=0)
    image = db.Column(db.String(120))  # file name under instance/uploads/products
    online = db.Column(db.Boolean, nullable=False, default=True)  # listed in the online shop
    # Online shop page and Google search (all optional; the name is used when empty)
    description = db.Column(db.Text)
    seo_title = db.Column(db.String(80))
    seo_description = db.Column(db.String(200))
    slug = db.Column(db.String(120))

    @property
    def url_slug(self):
        return slugify(self.slug or self.name)

    batches = db.relationship("Batch", back_populates="product", order_by="Batch.id")

    def cost_rate(self, taxed, purchase_rate=None):
        """Unit cost used for profit: before GST if GST is claimed back, else including it."""
        rate = D(self.purchase_rate if purchase_rate is None else purchase_rate)
        return money(rate if taxed else rate * (1 + D(self.gst_rate) / 100))

    def rate_for(self, price_list):
        if price_list == "wholesale" and D(self.wholesale_rate) > 0:
            return D(self.wholesale_rate)
        return D(self.sale_rate)

    @property
    def batches_in_stock(self):
        """Batches with stock, the one to sell first (earliest expiry, then oldest) first."""
        live = [b for b in self.batches if D(b.qty) > 0]
        return sorted(live, key=lambda b: (b.expiry or date.max, b.id))


class Batch(db.Model):
    """Stock received together: its own batch number, expiry and MRP. Products with
    batches keep product.stock equal to the sum of their batches."""

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False, index=True)
    batch_no = db.Column(db.String(40))
    expiry = db.Column(db.Date, index=True)
    mrp = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    sale_rate = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    purchase_rate = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    qty = db.Column(db.Numeric(12, 3), nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    product = db.relationship("Product", back_populates="batches")

    @property
    def label(self):
        parts = [f"MRP {D(self.mrp).normalize():f}"]
        if self.batch_no:
            parts.append(self.batch_no)
        if self.expiry:
            parts.append(f"exp {self.expiry:%m/%y}")
        return " · ".join(parts)


class Party(db.Model):
    """A customer or a supplier."""

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(10), nullable=False, index=True)  # customer | supplier
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(40))
    address = db.Column(db.Text)
    gstin = db.Column(db.String(20))
    price_list = db.Column(db.String(10), nullable=False, default="retail")  # retail | wholesale
    opening_balance = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # owed when you started
    # Online shop login (mobile number + password), for customers the admin approves.
    shop_access = db.Column(db.Boolean, nullable=False, default=False)
    shop_password_hash = db.Column(db.String(255))
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
        return money(D(self.opening_balance) + D(billed) - D(settled))

    def set_shop_password(self, password):
        self.shop_password_hash = generate_password_hash(password)

    def check_shop_password(self, password):
        return bool(self.shop_password_hash) and check_password_hash(self.shop_password_hash, password)

    @property
    def points(self):
        if self.kind != "customer":
            return 0
        earned, used = db.session.query(
            func.coalesce(func.sum(Sale.points_earned), 0), func.coalesce(func.sum(Sale.points_redeemed), 0)
        ).filter(Sale.party_id == self.id, Sale.status == "saved").one()
        return int(earned) - int(used)


class PartyPayment(db.Model):
    """Money received from a customer, or paid to a supplier, against their balance."""

    id = db.Column(db.Integer, primary_key=True)
    party_id = db.Column(db.Integer, db.ForeignKey("party.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
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

    @classmethod
    def next_value(cls, column):
        return (db.session.query(func.max(column)).scalar() or 0) + 1


class Sale(_Document, db.Model):
    """A bill (kind "sale") or a credit note for returned goods (kind "return", negative quantities)."""

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(10), nullable=False, default="sale")
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    user = db.relationship("User")
    number = db.Column(db.Integer, unique=True)  # bill number, assigned when saved
    return_number = db.Column(db.Integer, unique=True)  # credit note number
    return_of_id = db.Column(db.Integer, db.ForeignKey("sale.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now, index=True)
    party_id = db.Column(db.Integer, db.ForeignKey("party.id"))
    payment_mode = db.Column(db.String(10), nullable=False, default="cash")
    status = db.Column(db.String(10), nullable=False, default="draft", index=True)
    taxed = db.Column(db.Boolean, nullable=False, default=True)
    discount = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # entered by the cashier
    promo_discount = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # from a bill offer
    promo_note = db.Column(db.String(120))
    redeem_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # loyalty points used
    points_redeemed = db.Column(db.Integer, nullable=False, default=0)
    points_earned = db.Column(db.Integer, nullable=False, default=0)
    paid = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    gross = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    tax = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    round_off = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    net = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    profit = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    party = db.relationship("Party")
    return_of = db.relationship("Sale", remote_side=[id], backref="returns")
    items = db.relationship("SaleItem", back_populates="sale", cascade="all, delete-orphan", order_by="SaleItem.id")

    @property
    def is_return(self):
        return self.kind == "return"

    @property
    def doc_no(self):
        return f"CN-{self.return_number}" if self.is_return else str(self.number or "")

    @property
    def title(self):
        return f"Credit note {self.doc_no}" if self.is_return else f"Bill {self.doc_no}"

    @property
    def customer_name(self):
        return self.party.name if self.party else "Walk-in Customer"

    @property
    def price_list(self):
        return self.party.price_list if self.party else "retail"

    @property
    def total_discount(self):
        return money(D(self.discount) + D(self.promo_discount) + D(self.redeem_amount))

    @property
    def savings(self):
        """What the customer saved against MRP, for the receipt."""
        below_mrp = sum(
            ((D(i.mrp) - D(i.rate)) * D(i.qty) for i in self.items if D(i.mrp) > D(i.rate)), Decimal(0)
        )
        lines = sum((i.line_discount for i in self.items), Decimal(0))
        return money(below_mrp + lines + D(self.discount) + D(self.promo_discount))

    def add_product(self, product, qty=1, batch=None):
        """Add a product (from the batch to sell first), or increase its quantity if it is on the bill."""
        for item in self.items:
            if item.product_id == product.id and (batch is None or item.batch_id == batch.id):
                item.qty = D(item.qty) + D(qty)
                return item
        item = SaleItem(product=product, name=product.name, qty=qty, gst_rate=product.gst_rate)
        item.use_batch(batch or (product.batches_in_stock[:1] or [None])[0], self)
        self.items.append(item)
        return item

    def compute(self, promotions=None):
        """Recalculate every amount. While billing, pass the running offers to (re)apply them;
        with None, offer amounts already on the lines (e.g. on a credit note) are kept."""
        if promotions is not None:
            for item in self.items:
                item.promo_amount, item.promo_note = Decimal(0), None
                item.apply_best_offer(promotions)
        gross = sum((i.compute_amount() for i in self.items), Decimal(0))
        if promotions is not None:
            self.promo_discount, self.promo_note = Decimal(0), None
            for promo in promotions if gross > 0 else ():
                cut = promo.bill_discount(gross)
                if cut > self.promo_discount:
                    self.promo_discount, self.promo_note = cut, promo.name
        # Discounts follow the bill's sign: positive on sales, negative on credit notes.
        discount = money(self.discount) + D(self.promo_discount) + D(self.redeem_amount)
        discount = min(discount, gross) if gross > 0 else max(discount, gross) if gross < 0 else Decimal(0)
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
        self.gross, self.tax, self.profit = gross, money(tax), money(profit)
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
    batch_id = db.Column(db.Integer, db.ForeignKey("batch.id"))
    source_item_id = db.Column(db.Integer, db.ForeignKey("sale_item.id"))  # on a credit note: the line returned
    name = db.Column(db.String(160), nullable=False)
    qty = db.Column(db.Numeric(12, 3), nullable=False, default=1)  # negative = returned
    mrp = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    rate = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    disc_pct = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    promo_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # taken off by an offer
    promo_note = db.Column(db.String(120))
    gst_rate = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    cost = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # unit cost when sold
    amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # after line discounts, incl. GST
    revenue = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # after bill discount, excl. GST

    sale = db.relationship("Sale", back_populates="items")
    product = db.relationship("Product")
    batch = db.relationship("Batch")
    source_item = db.relationship("SaleItem", remote_side=[id])

    @property
    def line_discount(self):
        return money(D(self.qty) * D(self.rate) - D(self.amount))

    def use_batch(self, batch, sale):
        """Price the line from a batch (its MRP and rate) or, without batches, from the product."""
        self.batch = batch
        self.mrp = batch.mrp if batch else self.product.mrp
        if sale.price_list == "wholesale" and D(self.product.wholesale_rate) > 0:
            self.rate = self.product.wholesale_rate
        elif batch and D(batch.sale_rate) > 0:
            self.rate = batch.sale_rate
        else:
            self.rate = self.product.sale_rate
        self.cost = self.product.cost_rate(sale.taxed, batch.purchase_rate if batch else None)

    def apply_best_offer(self, promotions):
        best = Decimal(0)
        for promo in promotions:
            cut = promo.line_discount(self)
            if cut > best:
                best, self.promo_note = cut, promo.name
        self.promo_amount = best

    def compute_amount(self):
        self.amount = money(D(self.qty) * D(self.rate) * (1 - D(self.disc_pct) / 100) - D(self.promo_amount))
        return self.amount


class Promotion(db.Model):
    """An offer applied automatically while billing.
    percent: % off matching products; bxgy: buy X get Y free of the same product;
    bill: % or flat ₹ off when the bill reaches a minimum amount."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    kind = db.Column(db.String(10), nullable=False)  # percent | bxgy | bill
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"))  # percent/bxgy: one product...
    category = db.Column(db.String(80))  # ...or a whole category (neither = everything)
    percent = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    buy_qty = db.Column(db.Integer, nullable=False, default=0)
    free_qty = db.Column(db.Integer, nullable=False, default=0)
    min_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    flat_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    starts_on = db.Column(db.Date)
    ends_on = db.Column(db.Date)
    start_time = db.Column(db.Time)  # happy hours
    end_time = db.Column(db.Time)
    active = db.Column(db.Boolean, nullable=False, default=True)
    product = db.relationship("Product")

    @classmethod
    def running(cls, now=None):
        now = now or datetime.now()
        offers = []
        for promo in cls.query.filter_by(active=True):
            if promo.starts_on and now.date() < promo.starts_on or promo.ends_on and now.date() > promo.ends_on:
                continue
            if promo.start_time and promo.end_time and not promo.start_time <= now.time() <= promo.end_time:
                continue
            offers.append(promo)
        return offers

    def matches(self, item):
        if item.product is None:
            return False
        if self.product_id:
            return item.product_id == self.product_id
        if self.category:
            return (item.product.category or "").lower() == self.category.lower()
        return True

    def line_discount(self, item):
        qty = D(item.qty)
        if self.kind not in ("percent", "bxgy") or qty <= 0 or not self.matches(item):
            return Decimal(0)
        unit = D(item.rate) * (1 - D(item.disc_pct) / 100)
        if self.kind == "percent":
            return money(qty * unit * D(self.percent) / 100)
        group = self.buy_qty + self.free_qty
        if not self.buy_qty or not self.free_qty or qty < group:
            return Decimal(0)
        return money(int(qty // group) * self.free_qty * unit)

    def bill_discount(self, gross):
        if self.kind != "bill" or gross < D(self.min_amount):
            return Decimal(0)
        return max(money(gross * D(self.percent) / 100), money(self.flat_amount))

    @property
    def summary(self):
        target = self.product.name if self.product else f"category {self.category}" if self.category else "all products"
        if self.kind == "percent":
            return f"{D(self.percent).normalize():f}% off {target}"
        if self.kind == "bxgy":
            return f"Buy {self.buy_qty} get {self.free_qty} free on {target}"
        off = f"{D(self.percent).normalize():f}%" if D(self.percent) else f"₹{D(self.flat_amount).normalize():f}"
        return f"{off} off bills of ₹{D(self.min_amount).normalize():f} or more"


class Purchase(_Document, db.Model):
    """A supplier bill (draft -> saved), or a purchase order (status "order") that becomes one."""

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    user = db.relationship("User")
    number = db.Column(db.Integer, unique=True)
    order_number = db.Column(db.Integer, unique=True)
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

    @property
    def editable(self):
        return self.status in ("draft", "order")

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
    batch_id = db.Column(db.Integer, db.ForeignKey("batch.id"))  # set when saved
    batch_no = db.Column(db.String(40))
    expiry = db.Column(db.Date)
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
    batch = db.relationship("Batch")

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
    batch_id = db.Column(db.Integer, db.ForeignKey("batch.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now, index=True)
    system_qty = db.Column(db.Numeric(12, 3), nullable=False)
    actual_qty = db.Column(db.Numeric(12, 3), nullable=False)
    reason = db.Column(db.String(20), nullable=False, default="count")
    note = db.Column(db.String(200))
    product = db.relationship("Product")
    batch = db.relationship("Batch")

    @property
    def difference(self):
        return D(self.actual_qty) - D(self.system_qty)


class CashEntry(db.Model):
    """Cash put into or taken out of the drawer (opening cash, change float, owner withdrawal)."""

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    kind = db.Column(db.String(10), nullable=False)  # opening | in | out
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    note = db.Column(db.String(200))
    user = db.relationship("User")


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    category = db.Column(db.String(60), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    mode = db.Column(db.String(10), nullable=False, default="cash")
    note = db.Column(db.String(200))
    user = db.relationship("User")


class DayClose(db.Model):
    """End-of-day cash count for one cashier (or the whole shop when user is empty)."""

    id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.Date, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    closed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    expected = db.Column(db.Numeric(12, 2), nullable=False)
    counted = db.Column(db.Numeric(12, 2), nullable=False)
    note = db.Column(db.String(200))
    user = db.relationship("User", foreign_keys=[user_id])
    closed_by = db.relationship("User", foreign_keys=[closed_by_id])

    @property
    def difference(self):
        return money(D(self.counted) - D(self.expected))


ORDER_STEPS = {"new": "New", "confirmed": "Confirmed", "packed": "Packed", "out": "Out for delivery",
               "delivered": "Delivered"}


class Order(db.Model):
    """An order request from the online shop. While status is "cart" it is the customer's cart."""

    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.Integer, unique=True)  # given when placed
    party_id = db.Column(db.Integer, db.ForeignKey("party.id"), nullable=False, index=True)
    status = db.Column(db.String(12), nullable=False, default="cart", index=True)  # cart, ORDER_STEPS, cancelled
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    placed_at = db.Column(db.DateTime, index=True)
    updated_at = db.Column(db.DateTime)
    address = db.Column(db.Text)
    note = db.Column(db.String(300))  # from the customer
    staff_note = db.Column(db.String(300))  # e.g. reason for cancelling, shown to the customer
    sale_id = db.Column(db.Integer, db.ForeignKey("sale.id"))  # the bill made for it

    party = db.relationship("Party")
    sale = db.relationship("Sale")
    items = db.relationship("OrderItem", back_populates="order", cascade="all, delete-orphan",
                            order_by="OrderItem.id")

    @classmethod
    def next_number(cls):
        return (db.session.query(func.max(cls.number)).scalar() or 0) + 1

    @property
    def total(self):
        return money(sum((D(i.qty) * D(i.rate) for i in self.items), Decimal(0)))

    @property
    def status_label(self):
        return "Cancelled" if self.status == "cancelled" else ORDER_STEPS.get(self.status, "Cart")

    def add(self, product, qty, rate):
        for item in self.items:
            if item.product_id == product.id:
                item.qty = D(item.qty) + D(qty)
                return item
        item = OrderItem(product=product, name=product.name, qty=qty, rate=rate)
        self.items.append(item)
        return item


class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    name = db.Column(db.String(160), nullable=False)
    qty = db.Column(db.Numeric(12, 3), nullable=False, default=1)
    rate = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # price shown when ordered

    order = db.relationship("Order", back_populates="items")
    product = db.relationship("Product")

    @property
    def amount(self):
        return money(D(self.qty) * D(self.rate))
