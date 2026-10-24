import pytest

from billing import create_app
from billing.models import Customer, Product, db


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    with app.app_context():
        db.session.add_all([
            Customer(name="Acme Ltd", email="ap@acme.test"),
            Product(name="Widget", barcode="8901234567890", unit_price="2.50"),
            Product(name="Gadget", barcode="4006381333931", unit_price="10.00"),
        ])
        db.session.commit()
        yield app


@pytest.fixture
def client(app):
    return app.test_client()
