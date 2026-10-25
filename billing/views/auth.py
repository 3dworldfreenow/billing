from decimal import Decimal

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for

from ..models import PERMISSIONS, User, db
from ..services import log, parse_decimal

bp = Blueprint("auth", __name__)

# Blueprint -> permission needed to use it. Blueprints not listed only need a login.
BLUEPRINT_PERMISSION = {name: name for name in PERMISSIONS}
OPEN_ENDPOINTS = {"auth.login", "auth.setup", "static"}

HOME_FOR = [  # where each permission lands after login
    ("sales", "sales.pos"), ("reports", "reports.dashboard"), ("purchases", "purchases.new"),
    ("products", "products.index"), ("stock", "stock.index"), ("parties", "parties.customers"),
    ("cash", "cash.index"), ("offers", "offers.index"), ("settings", "settings.index"),
]


def home_url(user):
    return next((url_for(endpoint) for perm, endpoint in HOME_FOR if user.can(perm)), url_for("auth.account"))


def load_user():
    """Runs before every request: requires login and the section's permission."""
    g.user = None
    if request.endpoint in OPEN_ENDPOINTS:
        return None
    user_id = session.get("user_id")
    g.user = db.session.get(User, user_id) if user_id else None
    if g.user is not None and not g.user.active:
        g.user = None
    if request.blueprint in ("shop", "public"):
        return None  # customers have their own login; staff are only looked up (admin preview)
    if g.user is None:
        if User.query.first() is None:
            return redirect(url_for("auth.setup"))
        return redirect(url_for("auth.login", next=request.full_path if request.method == "GET" else None))
    permission = BLUEPRINT_PERMISSION.get(request.blueprint)
    if permission and not g.user.can(permission):
        abort(403)
    return None


def _admin_only():
    if not g.user.is_admin:
        abort(403)


def _safe_next(target):
    return target if target and target.startswith("/") and not target.startswith("//") else None


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """First run: create the admin account."""
    if User.query.first() is not None:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        user = User(is_admin=True, permissions="")
        error = _fill_user(user, request.form, new=True)
        if error:
            flash(error, "error")
        else:
            db.session.add(user)
            db.session.commit()
            session.clear()
            session["user_id"] = user.id
            flash("Admin account created. Next, set your store details.", "success")
            return redirect(url_for("settings.index"))
    return render_template("auth/setup.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = User.query.filter_by(username=request.form.get("username", "").strip().lower()).first()
        if user and user.active and user.check_password(request.form.get("password", "")):
            session.clear()
            session["user_id"] = user.id
            session.permanent = True
            return redirect(_safe_next(request.args.get("next")) or home_url(user))
        flash("Wrong username or password.", "error")
    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/account", methods=["GET", "POST"])
def account():
    """Any user can change their own password."""
    if request.method == "POST":
        if not g.user.check_password(request.form.get("current", "")):
            flash("Current password is wrong.", "error")
        elif len(request.form.get("password", "")) < 6:
            flash("New password must be at least 6 characters.", "error")
        else:
            g.user.set_password(request.form["password"])
            db.session.commit()
            flash("Password changed.", "success")
    return render_template("auth/account.html")


@bp.route("/users")
def users():
    _admin_only()
    return render_template("auth/users.html", users=User.query.order_by(User.name).all(), permissions=PERMISSIONS)


def _fill_user(user, form, new):
    username = form.get("username", "").strip().lower()
    name = form.get("name", "").strip()
    password = form.get("password", "")
    if not username or not username.isalnum():
        return "Username must be letters and numbers only."
    if not name:
        return "Name is required."
    if User.query.filter(User.username == username, User.id != user.id).first():
        return f"Username {username} is already taken."
    if (new or password) and len(password) < 6:
        return "Password must be at least 6 characters."
    user.username, user.name = username, name
    if password:
        user.set_password(password)
    return None


@bp.route("/users/new", methods=["GET", "POST"])
@bp.route("/users/<int:user_id>", methods=["GET", "POST"])
def user_form(user_id=None):
    _admin_only()
    user = db.get_or_404(User, user_id) if user_id else User(permissions="sales", active=True, is_admin=False)
    if request.method == "POST":
        error = _fill_user(user, request.form, new=user_id is None)
        is_admin = request.form.get("is_admin") == "1"
        active = request.form.get("active") == "1"
        max_discount = parse_decimal(request.form.get("max_discount", ""), Decimal(100))  # blank = no limit
        if not error and user.id == g.user.id and (not is_admin or not active):
            error = "You can't remove your own admin access or deactivate yourself."
        if not error and (max_discount is None or not 0 <= max_discount <= 100):
            error = "Discount limit must be between 0 and 100%."
        if error:
            flash(error, "error")
        else:
            user.is_admin, user.active, user.max_discount = is_admin, active, max_discount
            user.permissions = ",".join(p for p in PERMISSIONS if request.form.get(f"perm_{p}") == "1")
            db.session.add(user)
            log("user saved", f"{user.username}: {'admin' if is_admin else user.permissions or 'no access'}, "
                              f"discount ≤ {max_discount}%, {'active' if active else 'disabled'}")
            db.session.commit()
            flash(f"User {user.username} saved.", "success")
            return redirect(url_for("auth.users"))
    return render_template("auth/user_form.html", user=user, permissions=PERMISSIONS)
