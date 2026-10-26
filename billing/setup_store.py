"""Create a store's admin account from the command line (used by new_store.sh), so a new
store never sits open on its first-run setup page.

    INSTANCE_DIR=/path/to/data STORE_ADMIN_PASSWORD=... python -m billing.setup_store \\
        --store "P MART Jaipur" --name "Owner" --username admin
"""

import argparse
import os
import sys

from . import create_app
from .models import Setting, User, db


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create the admin account of a new store.")
    parser.add_argument("--store", required=True, help="store name shown on bills and the shop")
    parser.add_argument("--name", required=True, help="admin's name")
    parser.add_argument("--username", required=True, help="admin's login (letters and numbers)")
    args = parser.parse_args(argv)
    password = os.environ.get("STORE_ADMIN_PASSWORD", "")

    from .views.auth import _fill_user

    app = create_app()
    with app.app_context():
        if User.query.first() is not None:
            print("This store already has users; nothing changed.", file=sys.stderr)
            return 1
        user = User(is_admin=True, permissions="", active=True)
        error = _fill_user(user, {"username": args.username, "name": args.name, "password": password}, new=True)
        if error:
            print(error, file=sys.stderr)
            return 1
        Setting.get().store_name = args.store.strip()[:120] or "My Store"
        db.session.add(user)
        db.session.commit()
        print(f"Store '{Setting.get().store_name}' ready; admin login: {user.username}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
