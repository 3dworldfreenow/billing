import io
import json
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


# --- SEO mode, product pages and AI writing --------------------------------------------------


def seo_mode():
    store = Setting.get()
    store.shop_enabled = store.shop_public = True
    db.session.commit()


def test_seo_mode_lets_visitors_browse_but_order_needs_login(app, client):
    customer(client)
    anon = app.test_client()
    assert anon.get("/shop/").headers["Location"].endswith("/shop/login")  # off: login first
    assert b"Disallow: /\n" == anon.get("/robots.txt").data.split(b"User-agent: *\n")[1]
    seo_mode()
    page = anon.get("/shop/").data.decode()
    assert "Medimix Soap" in page and 'rel="canonical"' in page and "noindex" not in page
    assert anon.get("/").headers["Location"].endswith("/shop/")  # the domain opens the shop
    soap = Product.query.filter_by(barcode=SOAP).one()
    anon.post("/shop/cart/add", data={"product_id": soap.id, "qty": "2"})
    anon.post("/shop/cart/add", data={"product_id": soap.id, "qty": "1"})
    cart = anon.get("/shop/cart").data.decode()
    assert "₹126.00" in cart and "Log in to place order" in cart and "noindex" in cart
    resp = anon.post("/shop/cart", data={f"qty_{soap.id}": "3", "action": "place"})
    assert "/shop/login?next=" in resp.headers["Location"] and Order.query.count() == 0
    # Logging in brings the cart along; then the normal order flow applies.
    resp = anon.post("/shop/login?next=/shop/cart", data={"phone": "9876543210", "password": "ram12345"})
    assert resp.headers["Location"].endswith("/shop/cart")
    order = Order.query.filter_by(status="cart").one()
    assert [(i.name, i.qty) for i in order.items] == [("Medimix Soap 125g", 3)]
    anon.post("/shop/cart", data={"address": "Home", "action": "place"})
    assert order.status == "new"


def test_product_page_seo_tags_and_sitemap(app, client):
    seo_mode()
    soap = Product.query.filter_by(barcode=SOAP).one()
    soap.brand, soap.description = "Medimix", "Ayurvedic soap.\nWith 18 herbs."
    soap.seo_title, soap.seo_description = "Medimix Soap 125g – P MART", "Buy Medimix soap online."
    db.session.commit()
    anon = app.test_client()
    resp = anon.get(f"/shop/p/{soap.id}")
    assert resp.status_code == 301 and resp.headers["Location"].endswith(f"/shop/p/{soap.id}-medimix-soap-125g")
    page = anon.get(f"/shop/p/{soap.id}-medimix-soap-125g").data.decode()
    assert "<title>Medimix Soap 125g – P MART</title>" in page
    assert 'name="description" content="Buy Medimix soap online."' in page and "<p>With 18 herbs.</p>" in page
    data = json.loads(page.split('<script type="application/ld+json">')[1].split("</script>")[0])
    assert data["offers"]["price"] == "42.00" and data["gtin13"] == SOAP and data["brand"]["name"] == "Medimix"
    soap.online = False
    db.session.commit()
    assert anon.get(f"/shop/p/{soap.id}-medimix-soap-125g").status_code == 404
    sitemap = anon.get("/sitemap.xml").data.decode()
    assert "/shop/p/2-tata-salt-1kg" in sitemap and "medimix" not in sitemap
    assert "Sitemap: http://localhost/sitemap.xml" in anon.get("/robots.txt").data.decode()


def test_product_form_saves_seo_fields(client):
    soap = Product.query.filter_by(barcode=SOAP).one()
    form = {"name": soap.name, "barcode": SOAP, "mrp": "45", "sale_rate": "42", "purchase_rate": "30",
            "gst_rate": "18", "stock": "10", "online": "1", "description": " Good soap ",
            "seo_title": "Soap", "seo_description": "Buy  soap\n now", "slug": "Medimix Soap!"}
    client.post(f"/products/{soap.id}", data=form)
    assert (soap.description, soap.seo_title, soap.seo_description, soap.slug) == (
        "Good soap", "Soap", "Buy soap now", "medimix-soap")
    assert "Write with AI" not in client.get(f"/products/{soap.id}").data.decode()


def test_ai_write_and_model_list(client, monkeypatch):
    import billing.ai as ai

    sent = []

    def fake_request(url, key, body=None, timeout=45):
        sent.append((url, key, body))
        if url.endswith("/models?pageSize=1000"):
            return {"models": [{"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]},
                               {"name": "models/text-embedding-9", "supportedGenerationMethods": ["embedContent"]}]}
        answer = {"seo_title": "Tata Salt 1kg – P MART", "seo_description": "Iodised salt.", "slug": "Tata Salt 1 KG",
                  "description": "Tata Salt is iodised salt."}
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(answer)}]}}]}

    monkeypatch.setattr(ai, "_request", fake_request)
    assert client.post("/products/ai-write", data={"name": "Tata Salt"}).get_json()["error"].startswith("Add a Gemini")
    client.post("/settings/ai", data={"gemini_api_key": "key-123", "gemini_model": ""})
    assert Setting.get().gemini_api_key == "key-123"
    assert "Write with AI" in client.get("/products/new").data.decode()
    assert client.post("/settings/ai/models").get_json() == {"models": ["gemini-3.8-flash"]}
    result = client.post("/products/ai-write", data={"name": "Tata Salt 1kg", "brand": "Tata"}).get_json()
    assert result["slug"] == "tata-salt-1-kg" and result["seo_title"] == "Tata Salt 1kg – P MART"
    url, key, body = sent[-1]
    assert url.endswith("/models/gemini-3.8-flash:generateContent") and key == "key-123"
    prompt = body["contents"][0]["parts"][0]["text"]
    assert "- Name: Tata Salt 1kg" in prompt and "- Brand: Tata" in prompt and "Category" not in prompt
    client.post("/settings/ai", data={"gemini_api_key": "", "gemini_model": "x", "remove_key": "1"})
    assert Setting.get().gemini_api_key is None
    page = client.get("/settings/shop").data.decode()
    assert "key-123" not in page and "SEO mode" in page
