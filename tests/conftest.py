import os

import pytest

from billing import create_app
from billing.models import Customer, Product, db


@pytest.fixture
def app():
    # Set TEST_DATABASE_URL to run against Postgres (e.g. a throwaway Supabase or local DB).
    url = os.environ.get("TEST_DATABASE_URL", "sqlite://")
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": url})
    with app.app_context():
        db.session.add_all([
            Customer(name="Acme Ltd", email="ap@acme.test"),
            Product(name="Widget", barcode="8901234567890", unit_price="2.50"),
            Product(name="Gadget", barcode="4006381333931", unit_price="10.00"),
        ])
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()
