import os
from decimal import Decimal, InvalidOperation

from flask import Flask, abort, g, redirect, render_template, request, url_for
from flask_migrate import Migrate
from sqlalchemy import func, text

from auth import verified_email
from balances import group_balances
from models import Expense, ExpenseItem, Group, GroupMember, ItemShare, Settlement, User, db

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ["DATABASE_URL"]
app.config["TEMPLATES_AUTO_RELOAD"] = True
db.init_app(app)
migrate = Migrate(app, db)


# ---------- helpers ----------

@app.template_filter("money")
def money(cents):
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


def parse_cents(text, allow_zero=False):
    """'12.50' -> 1250. Blank -> 0 if allow_zero. None means invalid."""
    text = text.strip().replace("$", "").replace(",", "")
    if allow_zero and text == "":
        return 0
    try:
        d = Decimal(text)
    except InvalidOperation:
        return None
    if not d.is_finite() or d.as_tuple().exponent < -2:
        return None
    if d < 0 or (d == 0 and not allow_zero):
        return None
    return int(d * 100)


def safe_next(target):
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return None


def require_membership(group_id):
    membership = db.session.get(GroupMember, (group_id, g.user.id))
    if membership is None:
        abort(404)
    return membership


def group_members(group_id):
    return db.session.execute(
        db.select(User)
        .join(GroupMember, GroupMember.user_id == User.id)
        .where(GroupMember.group_id == group_id)
        .order_by(User.display_name)
    ).scalars().all()


def render_group(group_id, error=None):
    group = db.session.get(Group, group_id)
    members = group_members(group_id)
    expenses = db.session.execute(
        db.select(Expense, func.sum(ExpenseItem.amount_cents).label("total"))
        .join(ExpenseItem, ExpenseItem.expense_id == Expense.id)
        .where(Expense.group_id == group_id)
        .group_by(Expense.id)
        .order_by(Expense.spent_at.desc(), Expense.id.desc())
    ).all()
    settlements = db.session.execute(
        db.select(Settlement)
        .where(Settlement.group_id == group_id)
        .order_by(Settlement.created_at.desc())
    ).scalars().all()
    return render_template(
        "group.html",
        group=group,
        members=members,
        names={m.id: m.display_name for m in members},
        balances=group_balances(group_id),
        expenses=expenses,
        settlements=settlements,
        invite_url=url_for("join_group", code=group.invite_code,
                           _external=True, _scheme="https"),
        error=error,
    )


# ---------- auth ----------

@app.before_request
def load_user():
    g.email = verified_email()
    g.user = db.session.execute(
        db.select(User).filter_by(email=g.email)
    ).scalar_one_or_none()
    if g.user is None and request.endpoint not in ("welcome", "static"):
        return redirect(url_for("welcome", next=request.path))


@app.route("/welcome", methods=["GET", "POST"])
def welcome():
    next_url = safe_next(request.values.get("next"))
    if g.user is not None:
        return redirect(next_url or url_for("home"))
    error = None
    if request.method == "POST":
        name = request.form.get("display_name", "").strip()
        if not 1 <= len(name) <= 80:
            error = "Name must be 1–80 characters."
        else:
            db.session.add(User(email=g.email, display_name=name))
            db.session.commit()
            return redirect(next_url or url_for("home"))
    return render_template("welcome.html", error=error, next_url=next_url or "")


# ---------- groups ----------

@app.get("/")
def home():
    groups = db.session.execute(
        db.select(Group)
        .join(GroupMember, GroupMember.group_id == Group.id)
        .where(GroupMember.user_id == g.user.id)
        .order_by(Group.name)
    ).scalars().all()
    return render_template("home.html", groups=groups)


@app.post("/groups")
def create_group():
    name = request.form.get("name", "").strip()
    if not 1 <= len(name) <= 100:
        abort(400)
    group = Group(name=name, created_by=g.user.id)
    db.session.add(group)
    db.session.flush()
    db.session.add(GroupMember(group_id=group.id, user_id=g.user.id, role="owner"))
    db.session.commit()
    return redirect(url_for("group_detail", group_id=group.id))


@app.get("/groups/<int:group_id>")
def group_detail(group_id):
    require_membership(group_id)
    return render_group(group_id)


@app.route("/join/<code>", methods=["GET", "POST"])
def join_group(code):
    group = db.session.execute(
        db.select(Group).filter_by(invite_code=code)
    ).scalar_one_or_none()
    if group is None:
        abort(404)
    if db.session.get(GroupMember, (group.id, g.user.id)):
        return redirect(url_for("group_detail", group_id=group.id))
    if request.method == "POST":
        db.session.add(GroupMember(group_id=group.id, user_id=g.user.id))
        db.session.commit()
        return redirect(url_for("group_detail", group_id=group.id))
    return render_template("join.html", group=group)


# ---------- expenses ----------

@app.post("/groups/<int:group_id>/expenses")
def add_expense(group_id):
    require_membership(group_id)
    member_ids = {m.id for m in group_members(group_id)}
    form = request.form

    description = form.get("description", "").strip()
    tax_cents = parse_cents(form.get("tax", ""), allow_zero=True)
    tip_cents = parse_cents(form.get("tip", ""), allow_zero=True)
    tip_mode = form.get("tip_mode", "even")
    try:
        paid_by = int(form.get("paid_by", ""))
    except ValueError:
        abort(400)
    if tip_mode not in ("even", "proportional"):
        abort(400)

    error = None
    if not 1 <= len(description) <= 200:
        error = "Description must be 1–200 characters."
    elif paid_by not in member_ids:
        error = "The payer must be in this group."
    elif tax_cents is None or tip_cents is None:
        error = "Tax and tip must be amounts like 3.00, or left blank."

    # Item rows arrive as item-0-amount, item-3-desc, ... (gaps happen when rows are removed)
    indices = sorted({
        int(k.split("-")[1]) for k in form
        if k.startswith("item-") and k.split("-")[1].isdigit()
    })
    items = []
    for i in indices:
        if error:
            break
        desc = form.get(f"item-{i}-desc", "").strip()
        raw_amount = form.get(f"item-{i}-amount", "").strip()
        if not desc and not raw_amount:
            continue  # ignore completely blank rows
        amount = parse_cents(raw_amount)
        try:
            sharers = {int(x) for x in form.getlist(f"item-{i}-sharers")}
        except ValueError:
            abort(400)
        n = len(items) + 1
        if amount is None:
            error = f"Item {n}: enter a positive amount like 12.50."
        elif not sharers or not sharers <= member_ids:
            error = f"Item {n}: pick at least one person from this group."
        else:
            items.append((desc[:200] or description, amount, sharers))

    if not error and not items:
        error = "Add at least one item."
    elif not error and len(items) > 50:
        error = "That's more than 50 items. Split it into multiple expenses."
    if error:
        return render_group(group_id, error=error), 400

    expense = Expense(group_id=group_id, paid_by=paid_by, created_by=g.user.id,
                      description=description, tax_cents=tax_cents,
                      tip_cents=tip_cents, tip_mode=tip_mode)
    db.session.add(expense)
    db.session.flush()
    for desc, amount, sharers in items:
        item = ExpenseItem(expense_id=expense.id, description=desc, amount_cents=amount)
        db.session.add(item)
        db.session.flush()
        db.session.add_all([ItemShare(item_id=item.id, user_id=u) for u in sharers])
    db.session.commit()
    return redirect(url_for("group_detail", group_id=group_id))

@app.get("/db-check")
def db_check():
    version = db.session.execute(text("SELECT version()")).scalar()
    return f"DB OK: {version}"

# ---------- settlements ----------

@app.post("/groups/<int:group_id>/settlements")
def record_settlement(group_id):
    require_membership(group_id)
    member_ids = {m.id for m in group_members(group_id)}
    amount_cents = parse_cents(request.form.get("amount", ""))
    try:
        to_user = int(request.form.get("to_user", ""))
    except ValueError:
        abort(400)

    error = None
    if to_user not in member_ids or to_user == g.user.id:
        error = "Pick someone else in this group."
    elif amount_cents is None:
        error = "Enter a positive amount like 15.00."
    if error:
        return render_group(group_id, error=error), 400

    db.session.add(Settlement(group_id=group_id, from_user=g.user.id,
                              to_user=to_user, amount_cents=amount_cents))
    db.session.commit()
    return redirect(url_for("group_detail", group_id=group_id))


@app.post("/settlements/<int:settlement_id>/confirm")
def confirm_settlement(settlement_id):
    s = db.session.get(Settlement, settlement_id)
    # Only the receiver, only while pending. Anything else looks like "not found".
    if s is None or s.to_user != g.user.id or s.status != "pending":
        abort(404)
    s.status = "confirmed"
    s.confirmed_at = func.now()
    db.session.commit()
    return redirect(url_for("group_detail", group_id=s.group_id))


@app.post("/settlements/<int:settlement_id>/delete")
def delete_settlement(settlement_id):
    s = db.session.get(Settlement, settlement_id)
    # Pending only; sender may cancel, receiver may reject.
    if s is None or s.status != "pending" or g.user.id not in (s.from_user, s.to_user):
        abort(404)
    group_id = s.group_id
    db.session.delete(s)
    db.session.commit()
    return redirect(url_for("group_detail", group_id=group_id))