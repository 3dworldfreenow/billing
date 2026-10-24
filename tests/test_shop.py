import io
from decimal import Decimal

from PIL import Image

from billing.models import Order, Party, Product, Sale, Setting, db

SOAP, SALT = "8904018301680", "8901058000290"


def customer(client, name="Ramesh", phone="98765 43210", password="ram12345", wholesale=False):
    client.post("/customers/new", data={"name": name, "phone": phone, "wholesale": "1" if wholesale else ""})
    party = Party.query.filter_by(name=name).one()
    client.post(f"/parties/{party.id}/shop", data={"shop_access": "1", "password": password})
    return party


def shopper(app, phone="+91-9876543210", password="ram12345"):
    c = app.test_client()
    c.post("/shop/login", data={"phone": phone, "password": password})
    return c


def open_shop(minimum=0):
    store = Setting.get()
    store.shop_enabled, store.shop_min_order = True, minimum
    db.session.commit()


def test_shop_closed_by_default_admin_can_preview(app, client):
    customer(client)
    anon = app.test_client()
    assert anon.get("/shop/").headers["Location"].endswith("/shop/login")
    resp = anon.post("/shop/login", data={"phone": "9876543210", "password": "ram12345"}, follow_redirects=True)
    assert b"Online orders are closed" in resp.data
    page = client.get("/shop/").data.decode()  # admin
    assert "Admin preview" in page and "Medimix Soap" in page
    assert b"Preview only" in client.post("/shop/cart/add", data={"product_id": 1}, follow_redirects=True).data


def test_approval_needs_phone_and_password(client):
    client.post("/customers/new", data={"name": "No Phone"})
    party = Party.query.filter_by(name="No Phone").one()
    resp = client.post(f"/parties/{party.id}/shop", data={"shop_access": "1", "password": "abc12345"},
                       follow_redirects=True)
    assert b"mobile number first" in resp.data and not party.shop_access
    party.phone = "9000000000"
    db.session.commit()
    assert b"at least 6" in client.post(f"/parties/{party.id}/shop", data={"shop_access": "1", "password": "123"},
                                        follow_redirects=True).data
    client.post(f"/parties/{party.id}/shop", data={"shop_access": "1", "password": "secret99"})
    assert party.shop_access and party.check_shop_password("secret99")


def test_order_flow_from_cart_to_delivery(app, client):
    open_shop(minimum=50)
    ramesh = customer(client)
    shop = shopper(app)
    soap, salt = (Product.query.filter_by(barcode=b).one() for b in (SOAP, SALT))
    salt.online = False
    db.session.commit()
    page = shop.get("/shop/").data.decode()
    assert "Medimix Soap" in page and "Tata Salt" not in page and "Admin preview" not in page
    shop.post("/shop/cart/add", data={"product_id": soap.id, "qty": "1"})
    assert b"Minimum order" in shop.post("/shop/cart", data={"address": "12 MG Road", "action": "place"},
                                         follow_redirects=True).data
    cart = Order.query.filter_by(status="cart").one()
    resp = shop.post("/shop/cart", data={f"qty_{cart.items[0].id}": "2", "address": "12 MG Road",
                                         "note": "after 6", "action": "place"})
    order = db.session.get(Order, cart.id)
    assert (order.status, order.number, order.total, order.address) == ("new", 1, Decimal("84.00"), "12 MG Road")
    assert resp.headers["Location"].endswith(f"/shop/orders/{order.id}")
    assert ramesh.address == "12 MG Road"
    # Staff side: red count, details, make bill, steps.
    assert b'class="nav-count">1<' in client.get("/pos", follow_redirects=True).data
    assert b"after 6" in client.get(f"/orders/{order.id}").data
    resp = client.post(f"/orders/{order.id}/bill")
    sale = db.session.get(Sale, order.sale_id)
    assert resp.headers["Location"].endswith(f"/sales/{sale.id}") and sale.status == "draft"
    assert (sale.party_id, sale.items[0].qty, sale.net) == (ramesh.id, 2, Decimal("84"))
    assert order.status == "packed" and soap.stock == 10  # stock moves when the bill is saved
    client.post(f"/sales/{sale.id}/update", data={"action": "save"})
    assert soap.stock == 8
    client.post(f"/orders/{order.id}/status", data={"status": "delivered", "staff_note": "Delivered by Raju"})
    page = shop.get(f"/shop/orders/{order.id}").data.decode()
    assert "Delivered by Raju" in page and page.count('class="done"') == 5


def test_customer_cancels_new_order_only(app, client):
    open_shop()
    customer(client)
    shop = shopper(app)
    soap = Product.query.filter_by(barcode=SOAP).one()
    shop.post("/shop/cart/add", data={"product_id": soap.id})
    shop.post("/shop/cart", data={"address": "Home", "action": "place"})
    order = Order.query.filter_by(status="new").one()
    shop.post(f"/shop/orders/{order.id}", data={"action": "cancel"})
    assert order.status == "cancelled"
    client.post(f"/orders/{order.id}/status", data={"status": "confirmed"})
    shop.post(f"/shop/orders/{order.id}", data={"action": "cancel"})
    assert order.status == "confirmed"


def test_customers_are_kept_apart(app, client):
    open_shop()
    customer(client)
    customer(client, name="Sita", phone="9123456780", password="sita1234")
    ramesh, sita = shopper(app), shopper(app, "9123456780", "sita1234")
    soap = Product.query.filter_by(barcode=SOAP).one()
    ramesh.post("/shop/cart/add", data={"product_id": soap.id})
    ramesh.post("/shop/cart", data={"address": "Home", "action": "place"})
    order = Order.query.filter_by(status="new").one()
    assert sita.get(f"/shop/orders/{order.id}").status_code == 404
    assert b"Your cart is empty" in sita.get("/shop/cart").data
    # A customer login is not a staff login.
    assert "/login" in sita.get("/pos").headers["Location"]
    assert sita.get("/orders/").status_code == 302
    assert b"Wrong mobile number" in app.test_client().post(
        "/shop/login", data={"phone": "9123456780", "password": "wrong"}, follow_redirects=True).data


def test_blocked_customer_is_logged_out(app, client):
    open_shop()
    party = customer(client)
    shop = shopper(app)
    assert shop.get("/shop/").status_code == 200
    client.post(f"/parties/{party.id}/shop", data={})  # untick "can order online"
    assert shop.get("/shop/").headers["Location"].endswith("/shop/login")


def test_wholesale_customer_sees_wholesale_price(app, client):
    open_shop()
    salt = Product.query.filter_by(barcode=SALT).one()
    salt.wholesale_rate = Decimal("25")
    db.session.commit()
    customer(client, wholesale=True)
    assert "₹25.00" in shopper(app).get("/shop/?q=salt").data.decode()


def test_product_photo_upload_resize_and_remove(client):
    soap = Product.query.filter_by(barcode=SOAP).one()
    buf = io.BytesIO()
    Image.new("RGB", (2400, 1200), "orange").save(buf, "JPEG")
    buf.seek(0)
    form = {"name": soap.name, "barcode": SOAP, "mrp": "45", "sale_rate": "42", "purchase_rate": "30",
            "gst_rate": "18", "stock": "10", "online": "1"}
    client.post(f"/products/{soap.id}", data={**form, "image": (buf, "soap.jpg")}, content_type="multipart/form-data")
    assert soap.image and soap.image.endswith(".webp")
    photo = client.get(f"/shop/media/{soap.image}")
    assert photo.status_code == 200 and Image.open(io.BytesIO(photo.data)).size == (800, 400)
    assert b'class="thumb"' in client.get("/products/").data
    resp = client.post(f"/products/{soap.id}", data={**form, "image": (io.BytesIO(b"not a picture"), "x.jpg")},
                       content_type="multipart/form-data", follow_redirects=True)
    assert b"isn&#39;t a picture" in resp.data
    client.post(f"/products/{soap.id}", data={**form, "remove_image": "1"})
    assert soap.image is None


def test_shop_settings_and_staff_pages(client):
    resp = client.post("/settings/shop", data={"shop_enabled": "1", "shop_note": "Delivery 10-8", "shop_min_order": "100"},
                       follow_redirects=True)
    assert b"Online shop settings saved" in resp.data and Setting.get().shop_enabled
    assert b"Delivery 10-8" in client.get("/shop/").data
    for url in ["/settings/shop", "/orders/", "/orders/?show=delivered", "/shop/cart", "/products/new"]:
        assert client.get(url).status_code == 200, url
