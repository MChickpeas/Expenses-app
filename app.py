import os

from flask import Flask
from sqlalchemy import create_engine, text

app = Flask(__name__)

engine = create_engine(os.environ["DATABASE_URL"])


@app.get("/")
def home():
    return "Check 1"


@app.get("/db-check")
def db_check():
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version()")).scalar()
    return f"DB OK: {version}"
