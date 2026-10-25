"""Numbers for the dashboard: one period (day, week, month or financial year), the one
before it for comparison, and the series the charts draw. Sums run in SQL so a year of
bills stays quick."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func

from .models import PAYMENT_MODES, D, Expense, Party, PartyPayment, Purchase, Sale, SaleItem, db

PERIODS = {"day": "Daily", "week": "Weekly", "month": "Monthly", "year": "Yearly"}
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


@dataclass
class Period:
    kind: str
    start: date
    end: date  # exclusive

    @classmethod
    def around(cls, kind, day):
        if kind == "day":
            return cls(kind, day, day + timedelta(days=1))
        if kind == "week":
            start = day - timedelta(days=day.weekday())
            return cls(kind, start, start + timedelta(days=7))
        if kind == "year":  # Indian financial year, April to March
            year = day.year if day.month >= 4 else day.year - 1
            return cls(kind, date(year, 4, 1), date(year + 1, 4, 1))
        start = day.replace(day=1)
        return cls("month", start, (start + timedelta(days=32)).replace(day=1))

    @property
    def previous(self):
        return Period.around(self.kind, self.start - timedelta(days=1))

    @property
    def next(self):
        return Period.around(self.kind, self.end)

    @property
    def last(self):
        return self.end - timedelta(days=1)

    @property
    def label(self):
        last = self.last
        if self.kind == "day":
            return self.start.strftime("%a, %d %b %Y")
        if self.kind == "week":
            return f"{self.start:%d %b} – {last:%d %b %Y}"
        if self.kind == "year":
            return f"FY {self.start.year}-{str(last.year)[2:]}"
        return self.start.strftime("%B %Y")

    @property
    def compared_to(self):
        name = {"day": "yesterday" if self.start == date.today() else "the day before", "week": "last week",
                "month": "last month", "year": "last year"}[self.kind]
        return f"the same time {name}" if self.contains_today() else name

    def to_date(self):
        """The previous period cut to the same length as this one so far, so a month that is
        25 days old is compared with the first 25 days of last month, not all of it."""
        before = self.previous
        if not self.contains_today():
            return before
        start, _ = before.bounds()
        return Window(start, start + (datetime.now() - datetime.combine(self.start, time.min)))

    def contains_today(self):
        return self.start <= date.today() < self.end

    def bounds(self):
        return datetime.combine(self.start, time.min), datetime.combine(self.end, time.min)

    def buckets(self):
        """(key, short label, long label) for each point on the chart's x axis."""
        if self.kind == "day":
            return [(f"{h:02d}", _hour(h), f"{_hour(h)} – {_hour(h + 1)}") for h in range(24)]
        if self.kind == "year":
            months = [date(self.start.year + (m < 4), m, 1) for m in (*range(4, 13), 1, 2, 3)]
            return [(f"{m:%Y-%m}", MONTHS[m.month - 1], f"{m:%B %Y}") for m in months]
        days = (self.end - self.start).days
        out = []
        for i in range(days):
            d = self.start + timedelta(days=i)
            short = d.strftime("%a") if self.kind == "week" else str(d.day)
            out.append((d.isoformat(), short, d.strftime("%a, %d %b")))
        return out

    def bucket_expr(self, column):
        fmt = {"day": "%H", "year": "%Y-%m"}.get(self.kind, "%Y-%m-%d")
        return func.strftime(fmt, column)


@dataclass
class Window:
    start_at: datetime
    end_at: datetime

    def bounds(self):
        return self.start_at, self.end_at


def _hour(h):
    h %= 24
    return f"{h % 12 or 12}{'am' if h < 12 else 'pm'}"


def _f(value):
    return float(D(value))


def _saved(model, period):
    start, end = period.bounds()
    return model.status == "saved", model.created_at >= start, model.created_at < end


def sales_totals(period):
    row = db.session.query(
        func.count(Sale.id), func.coalesce(func.sum(Sale.net), 0), func.coalesce(func.sum(Sale.paid), 0),
        func.coalesce(func.sum(Sale.profit), 0),
    ).filter(*_saved(Sale, period), Sale.kind == "sale").one()
    returns = db.session.query(func.count(Sale.id), func.coalesce(func.sum(Sale.net), 0)).filter(
        *_saved(Sale, period), Sale.kind == "return").one()
    all_net, all_paid, all_profit = db.session.query(
        func.coalesce(func.sum(Sale.net), 0), func.coalesce(func.sum(Sale.paid), 0),
        func.coalesce(func.sum(Sale.profit), 0)).filter(*_saved(Sale, period)).one()
    net = D(all_net)
    return {"bills": row[0], "net": net, "paid": D(all_paid), "due": net - D(all_paid), "profit": D(all_profit),
            "avg": D(row[1]) / row[0] if row[0] else Decimal(0), "returns": returns[0], "returned": -D(returns[1])}


def purchase_totals(period):
    count, net, paid = db.session.query(
        func.count(Purchase.id), func.coalesce(func.sum(Purchase.net), 0), func.coalesce(func.sum(Purchase.paid), 0),
    ).filter(*_saved(Purchase, period)).one()
    return {"bills": count, "net": D(net), "paid": D(paid), "due": D(net) - D(paid)}


def expense_totals(period):
    start, end = period.bounds()
    count, total = db.session.query(func.count(Expense.id), func.coalesce(func.sum(Expense.amount), 0)).filter(
        Expense.created_at >= start, Expense.created_at < end).one()
    return {"count": count, "total": D(total)}


def outstanding(kind):
    """What customers owe us (kind="customer") or what we owe suppliers, as of now."""
    model = Sale if kind == "customer" else Purchase
    billed = dict(db.session.query(model.party_id, func.sum(model.net - model.paid)).filter(
        model.status == "saved", model.party_id.isnot(None)).group_by(model.party_id))
    settled = dict(db.session.query(PartyPayment.party_id, func.sum(PartyPayment.amount)).group_by(PartyPayment.party_id))
    total, people = Decimal(0), 0
    for party_id, opening in db.session.query(Party.id, Party.opening_balance).filter(Party.kind == kind):
        balance = D(opening) + D(billed.get(party_id)) - D(settled.get(party_id))
        if balance > 0:
            total += balance
            people += 1
    return {"total": total, "people": people}


def change(now, before):
    """Percent change, or None when there is nothing to compare with."""
    now, before = D(now), D(before)
    if before <= 0:
        return None
    return float((now - before) / before * 100)


def sales_series(period):
    key = period.bucket_expr(Sale.created_at)
    rows = dict(db.session.query(key, func.sum(Sale.net)).filter(*_saved(Sale, period)).group_by(key))
    return [_f(rows.get(k)) for k, _, _ in period.buckets()]


def trend(period):
    previous = period.previous
    now, before = sales_series(period), sales_series(previous)
    buckets = period.buckets()
    # Don't draw the future as zero sales: stop the line at the current hour/day/month.
    if period.contains_today():
        current = {"day": datetime.now().strftime("%H"), "year": date.today().strftime("%Y-%m")}.get(
            period.kind, date.today().isoformat())
        last = next((i for i, (k, _, _) in enumerate(buckets) if k == current), len(buckets) - 1)
        now = [v if i <= last else None for i, v in enumerate(now)]
    before += [None] * (len(buckets) - len(before))
    return {
        "labels": [short for _, short, _ in buckets], "titles": [long for _, _, long in buckets],
        "series": [{"name": period.label, "values": now, "color": 1},
                   {"name": previous.label, "values": before[:len(buckets)], "color": 0}],
    }


def payment_modes(period):
    rows = dict(db.session.query(Sale.payment_mode, func.sum(Sale.net)).filter(*_saved(Sale, period))
                .group_by(Sale.payment_mode))
    names = {"cash": "Cash", "upi": "UPI", "card": "Card", "credit": "Credit"}
    return [{"name": names[m], "value": _f(rows.get(m))} for m in PAYMENT_MODES]


def top_products(period, limit=6):
    amount = func.sum(SaleItem.amount)
    rows = (db.session.query(SaleItem.name, amount, func.sum(SaleItem.qty)).join(Sale)
            .filter(*_saved(Sale, period)).group_by(SaleItem.name).having(amount > 0)
            .order_by(amount.desc()).limit(limit))
    return [{"name": name, "value": _f(total), "qty": _f(qty)} for name, total, qty in rows]


def expense_split(period, limit=4):
    start, end = period.bounds()
    total = func.sum(Expense.amount)
    rows = [(c, _f(t)) for c, t in db.session.query(Expense.category, total).filter(
        Expense.created_at >= start, Expense.created_at < end).group_by(Expense.category).order_by(total.desc())]
    out = [{"name": c, "value": t} for c, t in rows[:limit]]
    if len(rows) > limit:
        out.append({"name": "Other", "value": sum(t for _, t in rows[limit:]), "other": True})
    return out
