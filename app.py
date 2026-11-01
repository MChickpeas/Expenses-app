import os

from flask import Flask, abort, g, redirect, render_template, request, url_for
from flask_migrate import Migrate
from sqlalchemy import text

from auth import verified_email
from models import Group, GroupMember, User, db

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ["DATABASE_URL"]
app.config["TEMPLATES_AUTO_RELOAD"] = True
db.init_app(app)
migrate = Migrate(app, db)


def safe_next(target):
    """Only allow redirects to paths on this site (blocks open-redirect tricks)."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return None


@app.before_request
def load_user():
    g.email = verified_email()
    g.user = db.session.execute(
        db.select(User).filter_by(email=g.email)
    ).scalar_one_or_none()
    if g.user is None and request.endpoint not in ("welcome", "static"):
        # Remember where they were headed (e.g. an invite link)
        return redirect(url_for("welcome", next=request.path))


def require_membership(group_id):
    """404 if the current user isn't in this group — don't even confirm it exists."""
    membership = db.session.get(GroupMember, (group_id, g.user.id))
    if membership is None:
        abort(404)
    return membership


@app.get("/")
def home():
    groups = db.session.execute(
        db.select(Group)
        .join(GroupMember, GroupMember.group_id == Group.id)
        .where(GroupMember.user_id == g.user.id)
        .order_by(Group.name)
    ).scalars().all()
    return render_template("home.html", groups=groups)


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


@app.post("/groups")
def create_group():
    name = request.form.get("name", "").strip()
    if not 1 <= len(name) <= 100:
        abort(400)
    group = Group(name=name, created_by=g.user.id)
    db.session.add(group)
    db.session.flush()  # sends the INSERT so group.id exists, without committing yet
    db.session.add(GroupMember(group_id=group.id, user_id=g.user.id, role="owner"))
    db.session.commit()  # both rows saved together, or neither
    return redirect(url_for("group_detail", group_id=group.id))


@app.get("/groups/<int:group_id>")
def group_detail(group_id):
    require_membership(group_id)
    group = db.session.get(Group, group_id)
    members = db.session.execute(
        db.select(User)
        .join(GroupMember, GroupMember.user_id == User.id)
        .where(GroupMember.group_id == group_id)
        .order_by(User.display_name)
    ).scalars().all()
    invite_url = url_for("join_group", code=group.invite_code,
                         _external=True, _scheme="https")
    return render_template("group.html", group=group, members=members,
                           invite_url=invite_url)


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


@app.get("/db-check")
def db_check():
    version = db.session.execute(text("SELECT version()")).scalar()
    return f"DB OK: {version}"