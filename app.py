import os

from flask import Flask, g, redirect, render_template, request, url_for
from flask_migrate import Migrate
from sqlalchemy import text

from auth import verified_email
from models import User, db

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ["DATABASE_URL"]
app.config["TEMPLATES_AUTO_RELOAD"] = True
db.init_app(app)
migrate = Migrate(app, db)


@app.before_request
def load_user():
    """Runs before every request: verify identity, load the user if they exist."""
    g.email = verified_email()
    g.user = db.session.execute(
        db.select(User).filter_by(email=g.email)
    ).scalar_one_or_none()
    if g.user is None and request.endpoint not in ("welcome", "static"):
        return redirect(url_for("welcome"))


@app.get("/")
def home():
    return render_template("home.html")


@app.route("/welcome", methods=["GET", "POST"])
def welcome():
    if g.user is not None:
        return redirect(url_for("home"))
    error = None
    if request.method == "POST":
        name = request.form.get("display_name", "").strip()
        if not 1 <= len(name) <= 80:
            error = "Name must be 1–80 characters."
        else:
            db.session.add(User(email=g.email, display_name=name))
            db.session.commit()
            return redirect(url_for("home"))
    return render_template("welcome.html", error=error)


@app.get("/db-check")
def db_check():
    version = db.session.execute(text("SELECT version()")).scalar()
    return f"DB OK: {version}"