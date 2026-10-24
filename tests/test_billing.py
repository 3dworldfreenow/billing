from decimal import Decimal

from billing.models import Invoice, Product, db


def make_invoice(client, tax_rate="10"):
    resp = client.post("/invoices/new", data={"customer_id": "1", "tax_rate": tax_rate})
    assert resp.status_code == 302
    return int(resp.headers["Location"].rsplit("/", 1)[1])


def test_scan_adds_product_and_increments_quantity(client):
    inv_id = make_invoice(client)
    for _ in range(3):
        client.post(f"/invoices/{inv_id}/scan", data={"barcode": "8901234567890"})
    client.post(f"/invoices/{inv_id}/scan", data={"barcode": " 4006381333931 "})

    invoice = db.session.get(Invoice, inv_id)
    assert [(i.description, i.quantity) for i in invoice.items] == [("Widget", 3), ("Gadget", 1)]
    assert invoice.subtotal == Decimal("17.50")
    assert invoice.tax == Decimal("1.75")
    assert invoice.total == Decimal("19.25")


def test_unknown_barcode_is_reported(client):
    inv_id = make_invoice(client)
    resp = client.post(f"/invoices/{inv_id}/scan", data={"barcode": "000"}, follow_redirects=True)
    assert b"No product with barcode" in resp.data
    assert db.session.get(Invoice, inv_id).items == []


def test_barcode_lookup_api(client):
    assert client.get("/api/products/barcode/8901234567890").get_json()["name"] == "Widget"
    assert client.get("/api/products/barcode/nope").status_code == 404


def test_duplicate_barcode_rejected(client):
    resp = client.post(
        "/products/new", data={"name": "Copy", "barcode": "8901234567890", "unit_price": "1"}, follow_redirects=True
    )
    assert b"already used" in resp.data
    assert Product.query.count() == 2


def test_non_draft_invoice_cannot_be_edited(client):
    inv_id = make_invoice(client)
    client.post(f"/invoices/{inv_id}/status", data={"status": "sent"})
    assert client.post(f"/invoices/{inv_id}/scan", data={"barcode": "8901234567890"}).status_code == 409


def test_custom_item_and_pages_render(client):
    inv_id = make_invoice(client, tax_rate="0")
    client.post(f"/invoices/{inv_id}/items", data={"description": "Labour", "quantity": "2", "unit_price": "40"})
    assert db.session.get(Invoice, inv_id).total == Decimal("80.00")
    for url in ["/invoices", f"/invoices/{inv_id}", "/customers", "/products", "/products/new", "/invoices/new"]:
        assert client.get(url).status_code == 200, url


def test_invalid_invoice_form(client):
    resp = client.post("/invoices/new", data={"customer_id": "", "tax_rate": "5"}, follow_redirects=True)
    assert b"Choose a customer" in resp.data
    assert Invoice.query.count() == 0
