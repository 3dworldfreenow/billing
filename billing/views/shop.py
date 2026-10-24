"""The online shop for customers the admin has approved (mobile number + password).
Its login is separate from staff logins. When the shop is switched off, only the admin
can open it, as a preview."""

import re
from datetime import datetime

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request,
                   send_from_directory, session, url_for)

from ..models import ORDER_STEPS, D, Order, Party, Product, Setting, db
from ..services import parse_decimal, product_query

bp = Blueprint("shop", __name__, url_prefix="/shop")
PAGE_SIZE = 24
OPEN = {"shop.login", "shop.media"}


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
    if request.endpoint in OPEN or g.customer or g.preview:
        return None
    return redirect(url_for("shop.login"))


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
            return redirect(url_for("shop.catalog"))
        else:
            flash("Wrong mobile number or password.", "error")
    return render_template("shop/login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.pop("customer_id", None)
    return redirect(url_for("shop.login"))


def _cart(create=False):
    if g.customer is None:
        return None
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
    return {"customer": g.get("customer"), "preview": g.get("preview"), "price": price,
            "cart_count": len(cart.items) if cart else 0}


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


@bp.route("/cart/add", methods=["POST"])
def add():
    back = request.form.get("next") or url_for("shop.catalog")
    back = back if back.startswith("/shop") else url_for("shop.catalog")
    if g.customer is None:
        flash("Preview only: customers add to cart after logging in.", "error")
        return redirect(back)
    product = db.get_or_404(Product, request.form.get("product_id", type=int))
    qty = parse_decimal(request.form.get("qty", "1"), D(1))
    if not product.online or qty is None or qty <= 0:
        abort(400)
    _cart(create=True).add(product, qty, price(product))
    db.session.commit()
    flash(f"{product.name} added to your cart.", "success")
    return redirect(back)


@bp.route("/cart", methods=["GET", "POST"])
def cart():
    cart = _cart()
    store = Setting.get()
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
