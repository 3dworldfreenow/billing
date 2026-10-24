import pytest

from billing import create_app
from billing.models import Product, Setting, db


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    with app.app_context():
        db.session.add_all([
            Product(name="Medimix Soap 125g", barcode="8904018301680", mrp="45", sale_rate="42",
                    purchase_rate="30", gst_rate="18", stock="10"),
            Product(name="Tata Salt 1kg", barcode="8901058000290", mrp="28", sale_rate="28",
                    purchase_rate="22", gst_rate="0", stock="50"),
            Product(name="Loose Sugar", barcode=None, unit="kg", mrp="48", sale_rate="45",
                    purchase_rate="40", gst_rate="5", stock="25"),
        ])
        Setting.get().store_name = "P MART"
        db.session.commit()
        yield app


@pytest.fixture
def client(app):
    client = app.test_client()
    client.post("/setup", data={"name": "Owner", "username": "admin", "password": "secret123"})
    return client


def new_sale(client):
    resp = client.get("/pos")
    assert resp.status_code == 302, resp.data
    return int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[1])
