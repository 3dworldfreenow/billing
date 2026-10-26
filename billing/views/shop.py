"""The online shop for customers the admin has approved (mobile number + password).
Its login is separate from staff logins. When the shop is switched off, only the admin
can open it, as a preview.

SEO mode (Settings → Online shop): anyone, including search engines, can browse the
catalog and product pages and fill a cart kept in their browser session. Placing the
order still needs an approved login; the guest cart moves into their account then."""

import re
from datetime import datetime

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request,
                   send_from_directory, session, url_for)

from ..models import ORDER_STEPS, D, Order, Party, Product, Setting, db, money
from ..services import parse_decimal, product_query

bp = Blueprint("shop", __name__, url_prefix="/shop")
PAGE_SIZE = 24
OPEN = {"shop.login", "shop.media"}
PUBLIC = {"shop.catalog", "shop.product", "shop.add", "shop.cart"}  # open to everyone in SEO mode


def _digits(phone):
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:]


@bp.before_request
def load_customer():
    g.customer = None
    customer_id = session.get("customer_id")
    if customer_id:
        party = db.session.get(Party, customer_id)
        if party and party.shop_access and Setting.get().shop_enabled:
            g.customer = party
        else:
            session.pop("customer_id", None)
    # Staff are loaded by the app; an admin may look around the shop without a customer login.
    g.preview = g.customer is None and g.get("user") is not None and g.user.is_admin
    g.public = is_public()
    g.guest = g.customer is None and not g.preview and g.public
    if request.endpoint in OPEN or g.customer or g.preview or (g.public and request.endpoint in PUBLIC):
        return None
    return redirect(url_for("shop.login"))


def is_public():
    store = Setting.get()
    return bool(store.shop_enabled and store.shop_public)


@bp.route("/media/<path:name>")
def media(name):
    from ..images import folder

    return send_from_directory(folder(), name, max_age=7 * 24 * 3600)


@bp.route("/login", methods=["GET", "POST"])
def login():
    store = Setting.get()
    if request.method == "POST":
        phone = _digits(request.form.get("phone"))
        password = request.form.get("password", "")
        party = next((p for p in Party.query.filter_by(kind="customer", shop_access=True)
                      if phone and _digits(p.phone) == phone), None)
        if not store.shop_enabled:
            flash("Online orders are closed right now. Please call the shop.", "error")
        elif party and party.check_shop_password(password):
            session["customer_id"] = party.id
            session.permanent = True
            g.customer = party
            _merge_guest_cart()
            back = request.args.get("next", "")
            return redirect(back if back.startswith("/shop") else url_for("shop.catalog"))
        else:
            flash("Wrong mobile number or password.", "error")
    return render_template("shop/login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.pop("customer_id", None)
    return redirect(url_for("shop.login"))


class GuestCart:
    """A visitor's cart in SEO mode: {product id: qty} in the session, shaped like an Order
    so the cart page shows it the same way."""

    class Line:
        def __init__(self, product, qty):
            self.id = self.product_id = product.id
            self.product, self.name, self.qty = product, product.name, qty
            self.rate = price(product)
            self.amount = money(self.rate * qty)

    def __init__(self):
        raw = session.get("guest_cart") or {}
        products = {p.id: p for p in Product.query.filter(Product.id.in_([int(k) for k in raw]), Product.online.is_(True))}
        self.items = [self.Line(products[int(k)], D(q)) for k, q in raw.items() if int(k) in products]
        self.address = self.note = None

    @property
    def total(self):
        return sum((i.amount for i in self.items), D(0))

    @staticmethod
    def save(lines):
        session["guest_cart"] = {str(pid): str(qty) for pid, qty in lines.items() if qty > 0}


def _merge_guest_cart():
    raw = session.pop("guest_cart", None)
    if not raw:
        return
    cart = _cart(create=True)
    for pid, qty in raw.items():
        product = db.session.get(Product, int(pid))
        if product and product.online:
            cart.add(product, D(qty), price(product))
    db.session.commit()


def _cart(create=False):
    if g.customer is None:
        return GuestCart() if g.get("guest") else None
    cart = Order.query.filter_by(party_id=g.customer.id, status="cart").first()
    if cart is None and create:
        cart = Order(party=g.customer, status="cart")
        db.session.add(cart)
    return cart


def price(product):
    return product.rate_for(g.customer.price_list if g.customer else "retail")


@bp.app_context_processor
def shop_globals():
    if request.blueprint != "shop":
        return {}
    cart = _cart()
    return {"customer": g.get("customer"), "preview": g.get("preview"), "guest": g.get("guest"),
            "public": g.get("public"), "price": price, "cart_count": len(cart.items) if cart else 0}


@bp.route("/")
def catalog():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    page = max(request.args.get("page", 1, type=int), 1)
    query = product_query(q).filter(Product.online.is_(True))
    if category:
        query = query.filter(Product.category == category)
    count = query.count()
    categories = [c for (c,) in db.session.query(Product.category).filter(Product.online.is_(True))
                  .distinct().order_by(Product.category) if c]
    cart = _cart()
    in_cart = {i.product_id: i.qty for i in cart.items} if cart else {}
    return render_template(
        "shop/catalog.html", products=query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all(), q=q,
        category=category, categories=categories, page=page, pages=max((count - 1) // PAGE_SIZE + 1, 1),
        count=count, in_cart=in_cart,
    )


@bp.route("/p/<int:product_id>-<slug>")
@bp.route("/p/<int:product_id>")
def product(product_id, slug=None):
    item = db.get_or_404(Product, product_id)
    if not item.online:
        abort(404)
    if slug != item.url_slug:  # one address per product for search engines
        return redirect(url_for("shop.product", product_id=item.id, slug=item.url_slug), 301)
    cart = _cart()
    in_cart = next((i.qty for i in cart.items if i.product_id == item.id), None) if cart else None
    related = (Product.query.filter(Product.online.is_(True), Product.category == item.category, Product.id != item.id)
               .order_by(Product.name).limit(6).all() if item.category else [])
    return render_template("shop/product.html", product=item, in_cart=in_cart, related=related,
                           structured_data=structured_data)


def structured_data(product):
    """schema.org Product data, so Google can show the price and stock in search results."""
    url = url_for("shop.product", product_id=product.id, slug=product.url_slug, _external=True)
    data = {
        "@context": "https://schema.org", "@type": "Product", "name": product.name, "url": url,
        "description": product.description or product.seo_description or product.name,
        "sku": product.barcode or str(product.id),
        "offers": {"@type": "Offer", "priceCurrency": "INR", "price": f"{price(product):.2f}", "url": url,
                   "availability": "https://schema.org/" + ("InStock" if D(product.stock) > 0 else "OutOfStock"),
                   "seller": {"@type": "Organization", "name": Setting.get().store_name}},
    }
    if product.image:
        data["image"] = [url_for("shop.media", name=product.image, _external=True)]
    if product.brand:
        data["brand"] = {"@type": "Brand", "name": product.brand}
    if product.category:
        data["category"] = product.category
    if product.barcode and product.barcode.isdigit() and len(product.barcode) == 13:
        data["gtin13"] = product.barcode
    return data


@bp.route("/cart/add", methods=["POST"])
def add():
    back = request.form.get("next") or url_for("shop.catalog")
    back = back if back.startswith("/shop") else url_for("shop.catalog")
    if g.customer is None and not g.guest:
        flash("Preview only: customers add to cart after logging in.", "error")
        return redirect(back)
    product = db.get_or_404(Product, request.form.get("product_id", type=int))
    qty = parse_decimal(request.form.get("qty", "1"), D(1))
    if not product.online or qty is None or qty <= 0:
        abort(400)
    if g.guest:
        lines = {i.product_id: i.qty for i in GuestCart().items}
        lines[product.id] = lines.get(product.id, D(0)) + qty
        GuestCart.save(lines)
    else:
        _cart(create=True).add(product, qty, price(product))
        db.session.commit()
    flash(f"{product.name} added to your cart.", "success")
    return redirect(back)


@bp.route("/cart", methods=["GET", "POST"])
def cart():
    cart = _cart()
    store = Setting.get()
    if request.method == "POST" and g.guest:
        lines = {}
        for item in cart.items:
            qty = parse_decimal(request.form.get(f"qty_{item.id}", ""), item.qty)
            if qty is not None and qty > 0 and request.form.get("remove") != str(item.id):
                lines[item.product_id] = qty
        GuestCart.save(lines)
        if request.form.get("action") == "place" and lines:
            flash("Please log in to place your order. New here? Call the shop to get your login.", "error")
            return redirect(url_for("shop.login", next=url_for("shop.cart")))
        return redirect(url_for("shop.cart"))
    if request.method == "POST" and cart:
        for item in list(cart.items):
            qty = parse_decimal(request.form.get(f"qty_{item.id}", ""), D(item.qty))
            if qty is None or qty <= 0 or request.form.get("remove") == str(item.id):
                cart.items.remove(item)
            else:
                item.qty = qty
                item.rate = price(item.product)  # always today's price
        cart.address = request.form.get("address", "").strip() or None
        cart.note = request.form.get("note", "").strip() or None
        if request.form.get("action") == "place":
            if not cart.items:
                flash("Your cart is empty.", "error")
            elif not cart.address:
                flash("Please enter the delivery address.", "error")
            elif cart.total < D(store.shop_min_order):
                flash(f"Minimum order is ₹{D(store.shop_min_order):,.0f}.", "error")
            else:
                cart.status, cart.placed_at = "new", datetime.now()
                cart.updated_at = cart.placed_at
                cart.number = Order.next_number()
                g.customer.address = g.customer.address or cart.address
                db.session.commit()
                current_app.logger.info("Online order %s placed by %s", cart.number, g.customer.name)
                flash(f"Order {cart.number} sent! The shop will call you to confirm.", "success")
                return redirect(url_for("shop.order", order_id=cart.id))
        db.session.commit()
        return redirect(url_for("shop.cart"))
    if cart and not cart.address and g.customer:
        cart.address = g.customer.address
    return render_template("shop/cart.html", cart=cart)


@bp.route("/orders")
def orders():
    if g.customer is None:
        return redirect(url_for("shop.catalog"))
    mine = Order.query.filter(Order.party_id == g.customer.id, Order.status != "cart").order_by(Order.id.desc())
    return render_template("shop/orders.html", orders=mine.all())


@bp.route("/orders/<int:order_id>", methods=["GET", "POST"])
def order(order_id):
    found = db.get_or_404(Order, order_id)
    if g.customer is None or found.party_id != g.customer.id or found.status == "cart":
        abort(404)
    if request.method == "POST" and request.form.get("action") == "cancel" and found.status == "new":
        found.status, found.updated_at = "cancelled", datetime.now()
        found.staff_note = "Cancelled by you."
        db.session.commit()
        flash(f"Order {found.number} cancelled.", "success")
    return render_template("shop/order.html", order=found, steps=ORDER_STEPS)
