import os
import re

from flask import Flask
from sqlalchemy import text

from .models import db


def _database_url(url):
    # Supabase (and most hosts) hand out postgres:// or postgresql:// URLs;
    # SQLAlchemy needs the driver named to use psycopg 3.
    return re.sub(r"^postgres(ql)?://", "postgresql+psycopg://", url)


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev"),
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "DATABASE_URL", f"sqlite:///{os.path.join(app.instance_path, 'billing.db')}"
        ),
        # Hosted Postgres drops idle connections; check before reusing one.
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
    )
    if config:
        app.config.update(config)
    app.config["SQLALCHEMY_DATABASE_URI"] = _database_url(app.config["SQLALCHEMY_DATABASE_URI"])

    os.makedirs(app.instance_path, exist_ok=True)
    db.init_app(app)

    from .routes import bp

    app.register_blueprint(bp)

    with app.app_context():
        db.create_all()
        if db.engine.dialect.name == "postgresql":
            # Supabase exposes the public schema through its Data API. Enabling
            # RLS with no policies closes that door; the app connects as the
            # table owner, which bypasses RLS.
            for table in db.metadata.sorted_tables:
                db.session.execute(text(f'ALTER TABLE "{table.name}" ENABLE ROW LEVEL SECURITY'))
            db.session.commit()

    @app.template_filter("money")
    def money(value):
        return f"{value:,.2f}"

    return app
