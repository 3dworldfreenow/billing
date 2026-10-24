import os

from flask import Flask

from .models import db


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev"),
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "DATABASE_URL", f"sqlite:///{os.path.join(app.instance_path, 'billing.db')}"
        ),
    )
    if config:
        app.config.update(config)

    os.makedirs(app.instance_path, exist_ok=True)
    db.init_app(app)

    from .routes import bp

    app.register_blueprint(bp)

    with app.app_context():
        db.create_all()

    @app.template_filter("money")
    def money(value):
        return f"{value:,.2f}"

    return app
