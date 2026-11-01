import os

from flask import Flask
from flask_migrate import Migrate
from sqlalchemy import text

from models import db

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ["DATABASE_URL"]
db.init_app(app)
migrate = Migrate(app, db)


@app.get("/")
def home():
    return "Check 1"


@app.get("/db-check")
def db_check():
    version = db.session.execute(text("SELECT version()")).scalar()
    return f"DB OK: {version}"
