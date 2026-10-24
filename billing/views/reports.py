from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from flask import Blueprint, Response, render_template, request, url_for
from sqlalchemy import func, or_

from ..exporting import XLSX, export_response
from ..models import PAYMENT_MODES, Batch, D, Expense, Party, Product, Purchase, PurchaseItem, Sale, SaleItem, db, money

bp = Blueprint("reports", __name__, url_prefix="/reports")
PAGE_SIZE = 100

REPORTS = [
    ("reports.sales", "Sales register", "Every bill with tax, discount, payment mode and profit"),
    ("reports.daily", "Daily summary", "Day-wise sales, cash/UPI/card/credit split and profit"),
    ("reports.products", "Product-wise sales", "Quantity, amount and profit for each product"),
    ("reports.purchases", "Purchase register", "Every purchase bill with tax and amount due"),
    ("reports.gst", "GST summary & GSTR-1", "CGST/SGST by slab; download GSTR-1 and GSTR-3B tables for your CA"),
    ("reports.profit", "Net profit", "Gross profit from sales minus expenses, by category"),
    ("reports.expenses", "Expenses", "Every expense with category and payment mode"),
    ("reports.categories", "Category margin", "Sales, cost, profit and margin % by category"),
    ("reports.movement", "Fast, slow & dead stock", "What sells, what doesn't, and days of stock left"),
    ("reports.expiry", "Expiry", "Batches expiring soon or already expired"),
    ("reports.stock", "Stock & valuation", "Current stock, low stock, value at cost and at MRP"),
    ("reports.dues", "Outstanding", "What customers owe you and what you owe suppliers, with WhatsApp reminders"),
]


def _parse_date(value, default):
    try:
        return date.fromisoformat(value) if value else default
    except ValueError:
        return default


def date_range(default_from=None):
    """Read ?from=&to= (inclusive dates). Returns datetimes [start, end) and template args."""
    today = date.today()
    date_from = _parse_date(request.args.get("from"), default_from or today.replace(day=1))
    date_to = _parse_date(request.args.get("to"), today)
    start = datetime.combine(date_from, time.min)
    end = datetime.combine(date_to + timedelta(days=1), time.min)
    return start, end, {"date_from": date_from, "date_to": date_to}


def _saved(model, start, end):
    return model.query.filter(model.status == "saved", model.created_at >= start, model.created_at < end)


def summarise(sales):
    total = defaultdict(Decimal)
    for sale in sales:
        total["bills"] += 1
        for field in ("gross", "tax", "net", "profit", "paid"):
            total[field] += D(getattr(sale, field))
        total["discount"] += sale.total_discount
        total[sale.payment_mode] += D(sale.net)
    total["due"] = total["net"] - total["paid"]
    return total


def table(title, columns, rows, totals=None, filters="dates", actions=(), **extra):
    """Render a report as a table, or download it with ?export=xlsx|csv."""
    fmt = request.args.get("export")
    if fmt in ("xlsx", "csv"):
        return export_response(title, columns, rows, totals, fmt)
    return render_template(
        "reports/table.html", title=title, columns=columns, rows=rows, totals=totals, filters=filters, actions=actions,
        **extra
    )


def link(value, endpoint, **params):
    return {"v": value, "url": url_for(endpoint, **params)}


@bp.route("/")
def dashboard():
    today = date.today()
    start = datetime.combine(today, time.min)
    month = datetime.combine(today.replace(day=1), time.min)
    end = start + timedelta(days=1)
    low = Product.query.filter(Product.min_stock > 0, Product.stock <= Product.min_stock)
    return render_template(
        "reports/dashboard.html",
        today_sales=summarise(_saved(Sale, start, end)),
        month_sales=summarise(_saved(Sale, month, end)),
        purchases_month=sum((D(p.net) for p in _saved(Purchase, month, end)), Decimal(0)),
        low_stock=low.order_by(Product.name).limit(10).all(),
        low_count=low.count(),
        recent=Sale.query.filter_by(status="saved").order_by(Sale.id.desc()).limit(8).all(),
        expiring=_expiring(30).limit(10).all(),
        expenses_month=D(db.session.query(func.sum(Expense.amount)).filter(Expense.created_at >= month).scalar()),
        modes=PAYMENT_MODES,
        reports=REPORTS,
    )


@bp.route("/sales")
def sales():
    start, end, args = date_range()
    bills = _saved(Sale, start, end).order_by(Sale.id).all()
    columns = [("Bill", "text"), ("Date", "text"), ("Customer", "text"), ("Cashier", "text"), ("Mode", "text"),
               ("Gross", "money"), ("Discount", "money"), ("Tax", "money"), ("Net", "money"),
               ("Due", "money"), ("Profit", "money")]
    rows = [[link(s.doc_no, "sales.bill", sale_id=s.id), s.created_at.strftime("%d-%m-%Y %H:%M"), s.customer_name,
             s.cashier_name, s.payment_mode.upper(), D(s.gross), s.total_discount, D(s.tax), D(s.net), s.due,
             D(s.profit)] for s in bills]
    t = summarise(bills)
    totals = [f"{len(bills)} bills", "", "", "", "", t["gross"], t["discount"], t["tax"], t["net"], t["due"],
              t["profit"]]
    return table("Sales register", columns, rows, totals, **args)


@bp.route("/daily")
def daily():
    start, end, args = date_range()
    by_day = defaultdict(list)
    for sale in _saved(Sale, start, end):
        by_day[sale.created_at.date()].append(sale)
    columns = [("Date", "text"), ("Bills", "qty")] + [(m.upper(), "money") for m in PAYMENT_MODES] + [
        ("Net sales", "money"), ("Tax", "money"), ("Profit", "money")]

    def line(label, t):
        return [label, t["bills"]] + [t[m] for m in PAYMENT_MODES] + [t["net"], t["tax"], t["profit"]]

    rows = [line(day.strftime("%d-%m-%Y (%a)"), summarise(by_day[day])) for day in sorted(by_day)]
    totals = line("Total", summarise(s for day in by_day.values() for s in day))
    return table("Daily summary", columns, rows, totals, **args)


@bp.route("/products")
def products():
    start, end, args = date_range()
    amount = func.sum(SaleItem.amount)
    result = (
        db.session.query(
            SaleItem.name,
            func.sum(SaleItem.qty),
            amount,
            func.sum(SaleItem.revenue - SaleItem.cost * SaleItem.qty),
        )
        .join(Sale)
        .filter(Sale.status == "saved", Sale.created_at >= start, Sale.created_at < end)
        .group_by(SaleItem.product_id, SaleItem.name)
        .order_by(amount.desc())
        .all()
    )
    columns = [("Product", "text"), ("Qty sold", "qty"), ("Amount", "money"), ("Profit", "money")]
    rows = [[name, D(qty), money(amt), money(profit)] for name, qty, amt, profit in result]
    totals = ["Total", "", sum((r[2] for r in rows), Decimal(0)), sum((r[3] for r in rows), Decimal(0))]
    return table("Product-wise sales", columns, rows, totals, **args)


@bp.route("/purchases")
def purchases():
    start, end, args = date_range()
    bills = _saved(Purchase, start, end).order_by(Purchase.id).all()
    columns = [("Entry", "text"), ("Date", "text"), ("Supplier", "text"), ("Supplier bill", "text"),
               ("Mode", "text"), ("Taxable", "money"), ("Tax", "money"), ("Net", "money"), ("Due", "money")]
    rows = [[link(p.number, "purchases.bill", purchase_id=p.id), p.created_at.strftime("%d-%m-%Y"), p.supplier_name,
             p.bill_no or "", p.payment_mode.upper(), D(p.gross), D(p.tax), D(p.net), p.due] for p in bills]
    totals = [f"{len(bills)} bills", "", "", "", ""] + [sum((r[i] for r in rows), Decimal(0)) for i in (5, 6, 7, 8)]
    return table("Purchase register", columns, rows, totals, **args)


@bp.route("/gst")
def gst():
    start, end, args = date_range()
    sales = dict(
        db.session.query(SaleItem.gst_rate, func.sum(SaleItem.revenue))
        .join(Sale)
        .filter(Sale.status == "saved", Sale.taxed.is_(True), Sale.created_at >= start, Sale.created_at < end)
        .group_by(SaleItem.gst_rate)
        .all()
    )
    purchases = {
        rate: (taxable, tax)
        for rate, taxable, tax in db.session.query(
            PurchaseItem.gst_rate, func.sum(PurchaseItem.taxable), func.sum(PurchaseItem.tax)
        )
        .join(Purchase)
        .filter(Purchase.status == "saved", Purchase.created_at >= start, Purchase.created_at < end)
        .group_by(PurchaseItem.gst_rate)
    }
    columns = [("GST slab", "text"), ("Sales taxable", "money"), ("Output CGST", "money"), ("Output SGST", "money"),
               ("Purchase taxable", "money"), ("Input CGST", "money"), ("Input SGST", "money"),
               ("Net GST payable", "money")]
    rows = []
    for rate in sorted({D(r) for r in sales} | {D(r) for r in purchases}):
        s_taxable = money(next((v for r, v in sales.items() if D(r) == rate), 0))
        p_taxable, p_tax = next(((money(t), money(x)) for r, (t, x) in purchases.items() if D(r) == rate), (0, 0))
        out_tax, in_tax = money(s_taxable * rate / 100), money(p_tax)
        out_c, in_c = money(out_tax / 2), money(in_tax / 2)
        rows.append([f"{rate.normalize():f}%", s_taxable, out_c, out_tax - out_c, D(p_taxable), in_c, in_tax - in_c,
                     out_tax - in_tax])
    totals = ["Total"] + [sum((r[i] for r in rows), Decimal(0)) for i in range(1, 8)]
    gstr = url_for("reports.gstr1", **{"from": args["date_from"], "to": args["date_to"]})
    return table("GST summary", columns, rows, totals, actions=[("⬇ GSTR-1 & 3B (Excel)", gstr)],
                 note="Intra-state: GST is split equally into CGST and SGST. Composition-scheme bills are excluded "
                      "from output tax.", **args)


@bp.route("/stock")
def stock():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    low = request.args.get("low") == "1"
    page = max(request.args.get("page", 1, type=int), 1)
    query = Product.query
    for word in q.split():
        like = f"%{word}%"
        query = query.filter(or_(Product.name.ilike(like), Product.barcode.like(f"{word}%"),
                                 Product.brand.ilike(like)))
    if category:
        query = query.filter(Product.category == category)
    if low:
        query = query.filter(Product.stock <= Product.min_stock)
    value_cost, value_mrp = query.filter(Product.stock > 0).with_entities(
        func.sum(Product.stock * Product.purchase_rate), func.sum(Product.stock * Product.mrp)
    ).one()
    count = query.count()
    query = query.order_by(Product.name)
    if not request.args.get("export"):
        query = query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    columns = [("Product", "text"), ("Barcode", "text"), ("Category", "text"), ("Stock", "qty"),
               ("Min", "qty"), ("Cost", "money"), ("MRP", "money"), ("Value at cost", "money")]
    rows = [[link(p.name, "products.edit", product_id=p.id), p.barcode or "", p.category or "", D(p.stock),
             D(p.min_stock), D(p.purchase_rate), D(p.mrp), money(max(D(p.stock), 0) * D(p.purchase_rate))]
            for p in query]
    totals = [f"{count} products", "", "", "", "", "", "", money(value_cost or 0)]
    categories = [c for (c,) in db.session.query(Product.category).distinct().order_by(Product.category) if c]
    return table(
        "Stock & valuation", columns, rows, totals, filters="stock",
        note=f"Stock value at MRP: ₹{money(value_mrp or 0)}",
        q=q, category=category, low=low, categories=categories,
        page=page, pages=max((count - 1) // PAGE_SIZE + 1, 1),
    )


@bp.route("/dues")
def dues():
    columns = [("Name", "text"), ("Type", "text"), ("Phone", "text"), ("Balance", "money")]
    rows = []
    for party in Party.query.order_by(Party.kind, Party.name):
        balance = party.balance
        if balance:
            label = "Customer owes you" if party.kind == "customer" else "You owe supplier"
            remind = link("WhatsApp reminder", "parties.remind", party_id=party.id) \
                if party.kind == "customer" and balance > 0 and party.phone else ""
            rows.append([link(party.name, "parties.detail", party_id=party.id), label, party.phone or "", balance,
                         remind])
    columns.append(("Remind", "text"))
    return table("Outstanding", columns, rows, filters=None)


def _expiring(days):
    limit = date.today() + timedelta(days=days)
    return Batch.query.filter(Batch.qty > 0, Batch.expiry.isnot(None), Batch.expiry <= limit).order_by(Batch.expiry)


@bp.route("/gstr1")
def gstr1():
    from ..gst import gst_workbook

    start, end, args = date_range()
    name = f"GST-{args['date_from']:%Y-%m-%d}-to-{args['date_to']:%Y-%m-%d}.xlsx"
    return Response(gst_workbook(start, end), mimetype=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@bp.route("/profit")
def profit():
    start, end, args = date_range()
    sales = _saved(Sale, start, end).all()
    revenue = sum((sum((D(i.revenue) for i in s.items), Decimal(0)) for s in sales), Decimal(0))
    gross_profit = sum((D(s.profit) for s in sales), Decimal(0))
    expenses = db.session.query(Expense.category, func.sum(Expense.amount)).filter(
        Expense.created_at >= start, Expense.created_at < end).group_by(Expense.category).order_by(Expense.category)
    columns = [("", "text"), ("Amount", "money")]
    rows = [["Sales (before GST, after discounts)", money(revenue)],
            ["Cost of goods sold", money(revenue - gross_profit)], ["Gross profit", money(gross_profit)]]
    total_expenses = Decimal(0)
    for category, amount in expenses:
        rows.append([f"Expense: {category}", -D(amount)])
        total_expenses += D(amount)
    rows.append(["Total expenses", -total_expenses])
    margin = f" ({(gross_profit - total_expenses) * 100 / revenue:.1f}% of sales)" if revenue else ""
    return table("Net profit", columns, rows, [f"NET PROFIT{margin}", money(gross_profit - total_expenses)], **args)


@bp.route("/expenses")
def expenses():
    start, end, args = date_range()
    items = Expense.query.filter(Expense.created_at >= start, Expense.created_at < end).order_by(Expense.id).all()
    columns = [("Date", "text"), ("Category", "text"), ("Mode", "text"), ("Note", "text"), ("By", "text"),
               ("Amount", "money")]
    rows = [[e.created_at.strftime("%d-%m-%Y"), e.category, e.mode.upper(), e.note or "",
             e.user.name if e.user else "", D(e.amount)] for e in items]
    return table("Expenses", columns, rows, [f"{len(rows)} expenses", "", "", "", "",
                                             sum((r[5] for r in rows), Decimal(0))], **args)


@bp.route("/categories")
def categories():
    start, end, args = date_range()
    category = func.coalesce(Product.category, "(none)")
    result = (
        db.session.query(category, func.sum(SaleItem.qty), func.sum(SaleItem.revenue),
                         func.sum(SaleItem.cost * SaleItem.qty))
        .join(Sale, SaleItem.sale_id == Sale.id).outerjoin(Product, SaleItem.product_id == Product.id)
        .filter(Sale.status == "saved", Sale.created_at >= start, Sale.created_at < end)
        .group_by(category).order_by(func.sum(SaleItem.revenue).desc()).all()
    )
    columns = [("Category", "text"), ("Qty sold", "qty"), ("Sales (before GST)", "money"), ("Cost", "money"),
               ("Profit", "money"), ("Margin %", "qty")]
    rows = []
    for name, qty, revenue, cost in result:
        revenue, cost = money(revenue), money(cost)
        margin = ((revenue - cost) * 100 / revenue).quantize(Decimal("0.1")) if revenue else Decimal(0)
        rows.append([name, D(qty), revenue, cost, revenue - cost, margin])
    total_rev = sum((r[2] for r in rows), Decimal(0))
    total_profit = sum((r[4] for r in rows), Decimal(0))
    margin = (total_profit * 100 / total_rev).quantize(Decimal("0.1")) if total_rev else ""
    return table("Category margin", columns, rows,
                 ["Total", "", total_rev, sum((r[3] for r in rows), Decimal(0)), total_profit, margin], **args)


@bp.route("/movement")
def movement():
    start, end, args = date_range(default_from=date.today() - timedelta(days=29))
    days = max((args["date_to"] - args["date_from"]).days + 1, 1)
    sold = {pid: (D(qty), last) for pid, qty, last in db.session.query(
        SaleItem.product_id, func.sum(SaleItem.qty), func.max(Sale.created_at)).join(Sale)
        .filter(Sale.status == "saved", Sale.created_at >= start, Sale.created_at < end)
        .group_by(SaleItem.product_id)}
    ranked = sorted((q for q, _ in sold.values() if q > 0), reverse=True)
    fast_from = ranked[max(len(ranked) // 5 - 1, 0)] if ranked else None  # top 20% of sellers
    show = request.args.get("show", "")
    columns = [("Product", "text"), ("Category", "text"), ("Status", "text"), ("Sold", "qty"), ("Per day", "qty"),
               ("Last sold", "text"), ("Stock", "qty"), ("Days of stock", "qty"), ("Stock value", "money")]
    rows = []
    for p in Product.query.order_by(Product.name):
        qty, last = sold.get(p.id, (Decimal(0), None))
        stock = D(p.stock)
        if qty <= 0 and stock <= 0:
            continue
        status = "Dead" if qty <= 0 else "Fast" if fast_from is not None and qty >= fast_from else "Slow"
        if show and status.lower() != show:
            continue
        per_day = (qty / days).quantize(Decimal("0.01"))
        cover = (stock / per_day).quantize(Decimal("1")) if per_day > 0 else ""
        rows.append([link(p.name, "products.edit", product_id=p.id), p.category or "", status, qty, per_day,
                     last.strftime("%d-%m-%Y") if last else "—", stock, cover,
                     money(max(stock, 0) * D(p.purchase_rate))])
    rows.sort(key=lambda r: ({"Fast": 0, "Slow": 1, "Dead": 2}[r[2]], -r[3]))
    return table("Fast, slow & dead stock", columns, rows, filters="movement", show=show,
                 note="Fast = top 20% of sellers. Dead = has stock but no sale in this period.", **args)


@bp.route("/expiry")
def expiry():
    days = max(request.args.get("days", 60, type=int), 0)
    today = date.today()
    columns = [("Product", "text"), ("Batch", "text"), ("Expiry", "text"), ("Days left", "text"), ("Qty", "qty"),
               ("MRP", "money"), ("Value at cost", "money")]
    rows = []
    for b in _expiring(days):
        left = (b.expiry - today).days
        rows.append([link(b.product.name, "stock.index", product_id=b.product_id), b.batch_no or "",
                     b.expiry.strftime("%d-%m-%Y"), "EXPIRED" if left < 0 else str(left), D(b.qty), D(b.mrp),
                     money(D(b.qty) * D(b.purchase_rate))])
    return table("Expiry", columns, rows, ["Total", "", "", "", sum((r[4] for r in rows), Decimal(0)), "",
                                           sum((r[6] for r in rows), Decimal(0))],
                 filters="expiry", days=days,
                 note="Write off expired stock from Stock adjust → Write off → Expired.")
