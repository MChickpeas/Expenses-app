import secrets

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import (
    CheckConstraint, ForeignKey, ForeignKeyConstraint,
    MetaData, UniqueConstraint, func,
)

# Predictable constraint names, so future migrations can find and change them
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
db = SQLAlchemy(metadata=MetaData(naming_convention=convention))

GM = ["group_members.group_id", "group_members.user_id"]


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, unique=True)
    display_name = db.Column(db.String(80), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now())


class Group(db.Model):
    __tablename__ = "groups"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    invite_code = db.Column(db.String(32), nullable=False, unique=True,
                            default=lambda: secrets.token_urlsafe(8))
    created_by = db.Column(db.Integer, ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now())


class GroupMember(db.Model):
    __tablename__ = "group_members"
    group_id = db.Column(db.Integer, ForeignKey("groups.id"), primary_key=True)
    user_id = db.Column(db.Integer, ForeignKey("users.id"), primary_key=True)
    role = db.Column(db.String(10), nullable=False, server_default="member")
    joined_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'member')", name="role_valid"),
    )


class Trip(db.Model):
    __tablename__ = "trips"
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, ForeignKey("groups.id"), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("id", "group_id"),  # target for the composite FK below
    )


class Expense(db.Model):
    __tablename__ = "expenses"
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, nullable=False)
    trip_id = db.Column(db.Integer, nullable=True)
    paid_by = db.Column(db.Integer, nullable=False)
    created_by = db.Column(db.Integer, ForeignKey("users.id"), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    tax_cents = db.Column(db.Integer, nullable=False, server_default="0")
    tip_cents = db.Column(db.Integer, nullable=False, server_default="0")
    tip_mode = db.Column(db.String(12), nullable=False, server_default="proportional")
    spent_at = db.Column(db.Date, nullable=False, server_default=func.current_date())
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        # trip must belong to this same group
        ForeignKeyConstraint(["trip_id", "group_id"], ["trips.id", "trips.group_id"]),
        # payer must be a member of this group
        ForeignKeyConstraint(["group_id", "paid_by"], GM),
        CheckConstraint("tax_cents >= 0", name="tax_nonneg"),
        CheckConstraint("tip_cents >= 0", name="tip_nonneg"),
        CheckConstraint("tip_mode IN ('even', 'proportional')", name="tip_mode_valid"),
    )


class ExpenseItem(db.Model):
    __tablename__ = "expense_items"
    id = db.Column(db.Integer, primary_key=True)
    expense_id = db.Column(db.Integer, ForeignKey("expenses.id", ondelete="CASCADE"), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)
    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="amount_positive"),
    )


class ItemShare(db.Model):
    __tablename__ = "item_shares"
    item_id = db.Column(db.Integer, ForeignKey("expense_items.id", ondelete="CASCADE"), primary_key=True)
    user_id = db.Column(db.Integer, ForeignKey("users.id"), primary_key=True)


class Settlement(db.Model):
    __tablename__ = "settlements"
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, nullable=False)
    from_user = db.Column(db.Integer, nullable=False)
    to_user = db.Column(db.Integer, nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(10), nullable=False, server_default="pending")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=func.now())
    confirmed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    __table_args__ = (
        ForeignKeyConstraint(["group_id", "from_user"], GM),
        ForeignKeyConstraint(["group_id", "to_user"], GM),
        CheckConstraint("amount_cents > 0", name="amount_positive"),
        CheckConstraint("from_user <> to_user", name="not_self"),
        CheckConstraint("status IN ('pending', 'confirmed')", name="status_valid"),
        CheckConstraint("(status = 'confirmed') = (confirmed_at IS NOT NULL)",
                        name="confirmed_has_timestamp"),
    )
