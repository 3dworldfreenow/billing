from decimal import Decimal

from billing.models import Party, Product, Sale, Setting, db
from conftest import new_sale


def product(barcode):
    return Product.query.filter_by(barcode=barcode).one()


def test_scan_builds_bill_with_inclusive_gst_and_round_off(client):
    sale_id = new_sale(client)
    client.post(f"/sales/{sale_id}/scan", data={"code": "8904018301680"})
    client.post(f"/sales/{sale_id}/scan", data={"code": "8904018301680"})  # same item again -> qty 2
    client.post(f"/sales/{sale_id}/scan", data={"code": "3*8901058000290"})  # 3 x salt
    sale = db.session.get(Sale, sale_id)
    assert [(i.name, i.qty) for i in sale.items] == [("Medimix Soap 125g", 2), ("Tata Salt 1kg", 3)]
    assert sale.gross == Decimal("168.00")
    # soap 84.00 incl. 18% GST -> taxable 71.19, tax 12.81; salt is 0%
    assert sale.tax == Decimal("12.81")
    assert sale.net == Decimal("168")


def test_save_reduces_stock_records_cashier_profit_and_numbers(client):
    sale_id = new_sale(client)
    client.post(f"/sales/{sale_id}/scan", data={"code": "8904018301680"})
    item = db.session.get(Sale, sale_id).items[0]
    resp = client.post(f"/sales/{sale_id}/update", data={f"qty_{item.id}": "4", "discount": "8", "action": "save"})
    assert resp.status_code == 302
    sale = db.session.get(Sale, sale_id)
    assert sale.status == "saved" and sale.number == 1
    assert sale.cashier_name == "Owner"
    assert sale.net == Decimal("160") and sale.paid == Decimal("160")
    assert product("8904018301680").stock == Decimal("6")
    # revenue ex-GST 160/1.18 = 135.59, cost 4 x 30 = 120
    assert sale.profit == Decimal("15.59")

    second = new_sale(client)
    client.post(f"/sales/{second}/scan", data={"code": "8901058000290"})
    client.post(f"/sales/{second}/update", data={"action": "save"})
    assert db.session.get(Sale, second).number == 2


def test_name_search_picker_and_loose_weight(client):
    sale_id = new_sale(client)
    resp = client.post(f"/sales/{sale_id}/scan", data={"code": "sugar"})
    assert resp.status_code == 302  # single match is added directly
    sale = db.session.get(Sale, sale_id)
    item = sale.items[0]
    client.post(f"/sales/{sale_id}/update", data={f"qty_{item.id}": "1.250", "action": "update"})
    assert db.session.get(Sale, sale_id).items[0].amount == Decimal("56.25")

    resp = client.post(f"/sales/{sale_id}/scan", data={"code": "a"}, follow_redirects=True)
    assert b"Products matching" in resp.data


def test_unknown_barcode(client):
    sale_id = new_sale(client)
    resp = client.post(f"/sales/{sale_id}/scan", data={"code": "000111"}, follow_redirects=True)
    assert "No product found".encode() in resp.data


def test_credit_bill_needs_customer_and_tracks_balance(client):
    sale_id = new_sale(client)
    client.post(f"/sales/{sale_id}/scan", data={"code": "8901058000290"})
    resp = client.post(f"/sales/{sale_id}/update", data={"payment_mode": "credit", "action": "save"},
                       follow_redirects=True)
    assert b"Choose a customer" in resp.data
    client.post(f"/sales/{sale_id}/customer", data={"name": "Ramesh", "phone": "98765 43210"})
    customer = Party.query.filter_by(name="Ramesh").one()
    client.post(f"/sales/{sale_id}/update", data={"party_id": customer.id, "payment_mode": "credit", "paid": "10",
                                                  "action": "save"})
    assert customer.balance == Decimal("18.00")
    client.post(f"/parties/{customer.id}/payment", data={"amount": "18", "mode": "upi"})
    assert customer.balance == Decimal("0.00")


def test_return_with_negative_qty_and_cancel(client):
    sale_id = new_sale(client)
    client.post(f"/sales/{sale_id}/scan", data={"code": "-1*8904018301680"})
    client.post(f"/sales/{sale_id}/update", data={"action": "save"})
    sale = db.session.get(Sale, sale_id)
    assert sale.net == Decimal("-42") and product("8904018301680").stock == Decimal("11")
    client.post(f"/sales/{sale_id}/cancel")
    assert db.session.get(Sale, sale_id).status == "cancelled"
    assert product("8904018301680").stock == Decimal("10")


def test_composition_dealer_has_no_tax(client):
    Setting.get().gst_on_sales = False
    db.session.commit()
    sale_id = new_sale(client)
    client.post(f"/sales/{sale_id}/scan", data={"code": "8904018301680"})
    client.post(f"/sales/{sale_id}/update", data={"action": "save"})
    sale = db.session.get(Sale, sale_id)
    assert sale.tax == 0
    assert sale.profit == Decimal("6.60")  # 42 - (30 + 18% GST)


def test_receipt_invoice_whatsapp(client):
    sale_id = new_sale(client)
    client.post(f"/sales/{sale_id}/scan", data={"code": "8904018301680"})
    resp = client.post(f"/sales/{sale_id}/update", data={"action": "print"})
    assert "/receipt" in resp.headers["Location"]
    receipt = client.get(resp.headers["Location"])
    assert b"P MART" in receipt.data and b"TAX INVOICE" not in receipt.data  # no GSTIN set -> cash memo
    assert b"You saved" in receipt.data
    assert b"Medimix" in client.get(f"/sales/{sale_id}/invoice").data
    wa = client.get(f"/sales/{sale_id}/whatsapp?phone=98765-43210")
    assert wa.headers["Location"].startswith("https://api.whatsapp.com/send?text=")
    assert wa.headers["Location"].endswith("&phone=919876543210")
    assert "P%20MART" in wa.headers["Location"]


def test_upi_qr_shown_when_upi_id_set(client):
    Setting.get().upi_id = "pmart@okaxis"
    db.session.commit()
    sale_id = new_sale(client)
    client.post(f"/sales/{sale_id}/scan", data={"code": "8904018301680"})
    client.post(f"/sales/{sale_id}/update", data={"payment_mode": "upi", "action": "update"})
    assert b"<svg" in client.get(f"/sales/{sale_id}").data


def test_bill_page_after_scan_renders(client):
    sale_id = new_sale(client)
    resp = client.post(f"/sales/{sale_id}/scan", data={"code": "8904018301680"}, follow_redirects=True)
    assert resp.status_code == 200 and b"flash-row" in resp.data
    purchase_id = int(client.get("/purchases/new").headers["Location"].rsplit("/", 1)[1])
    resp = client.post(f"/purchases/{purchase_id}/scan", data={"code": "8904018301680"}, follow_redirects=True)
    assert resp.status_code == 200 and b"flash-row" in resp.data


def test_cgst_sgst_add_up_to_tax(client):
    sale_id = new_sale(client)
    client.post(f"/sales/{sale_id}/scan", data={"code": "4*8904018301680"})  # 168 incl. 18% -> tax 25.63
    client.post(f"/sales/{sale_id}/update", data={"action": "save"})
    (taxable, cgst, sgst), = db.session.get(Sale, sale_id).tax_summary().values()
    assert (taxable, cgst, sgst) == (Decimal("142.37"), Decimal("12.82"), Decimal("12.81"))


def test_whatsapp_text_is_formatted_for_phones(client):
    from billing.views.sales import bill_text

    sale_id = new_sale(client)
    client.post(f"/sales/{sale_id}/scan", data={"code": "4*8904018301680"})
    client.post(f"/sales/{sale_id}/scan", data={"code": "8901058000290"})
    client.post(f"/sales/{sale_id}/update", data={"discount": "5", "action": "save"})
    text = bill_text(db.session.get(Sale, sale_id))
    head, block, totals = text.split("```")
    assert head.startswith("*P MART*") and "*Bill No: 1*" in head
    rows = block.strip("\n").split("\n")
    assert all(len(r) <= 32 for r in rows)
    assert "MRP 45          4 x 42    168.00" in rows
    assert "*TOTAL: ₹191.00*" in totals and "Discount: -₹5.00" in totals and "You saved" in totals


def test_whatsapp_messages_have_no_emojis(client):
    """Emojis turn into '?' boxes on some WhatsApp apps; messages stick to plain text and ₹."""
    import re
    from urllib.parse import parse_qs, urlparse

    sale_id = new_sale(client)
    client.post(f"/sales/{sale_id}/scan", data={"code": "2*8904018301680"})
    client.post(f"/sales/{sale_id}/update", data={"payment_mode": "upi", "action": "save"})
    url = client.get(f"/sales/{sale_id}/whatsapp?phone=9575936429").headers["Location"]
    text = parse_qs(urlparse(url).query)["text"][0]
    assert "\n" in text and "*TOTAL: ₹84.00*" in text and "Paid by UPI" in text
    assert not re.search("[\U0001F000-\U0001FAFF☀-➿]", text)
