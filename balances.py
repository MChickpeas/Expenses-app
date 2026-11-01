from collections import defaultdict

from models import Expense, ExpenseItem, GroupMember, ItemShare, Settlement, db


def split_evenly(amount_cents, user_ids, poster_id, payer_id):
    users = sorted(user_ids)
    base, remainder = divmod(amount_cents, len(users))
    shares = {u: base for u in users}
    if poster_id in shares:
        lucky = poster_id
    elif payer_id in shares:
        lucky = payer_id
    else:
        lucky = users[0]
    shares[lucky] += remainder
    return shares


def group_balances(group_id):
    """Balance = paid − share (+ confirmed settlements). Positive = owed money."""
    balances = defaultdict(int)
    member_ids = db.session.execute(
        db.select(GroupMember.user_id).where(GroupMember.group_id == group_id)
    ).scalars()
    for uid in member_ids:
        balances[uid] = 0

    expenses = db.session.execute(
        db.select(Expense).where(Expense.group_id == group_id)
    ).scalars().all()
    for exp in expenses:
        if exp.tax_cents or exp.tip_cents:
            raise NotImplementedError("Tax/tip splitting isn't built yet")
        items = db.session.execute(
            db.select(ExpenseItem).where(ExpenseItem.expense_id == exp.id)
        ).scalars().all()
        for item in items:
            sharers = db.session.execute(
                db.select(ItemShare.user_id).where(ItemShare.item_id == item.id)
            ).scalars().all()
            balances[exp.paid_by] += item.amount_cents
            for uid, cents in split_evenly(item.amount_cents, sharers, exp.created_by, exp.paid_by).items():
                balances[uid] -= cents

    settlements = db.session.execute(
        db.select(Settlement).where(
            Settlement.group_id == group_id, Settlement.status == "confirmed"
        )
    ).scalars().all()
    for s in settlements:
        balances[s.from_user] += s.amount_cents
        balances[s.to_user] -= s.amount_cents

    total = sum(balances.values())
    if total != 0:
        raise RuntimeError(f"Balances sum to {total}, not 0: {dict(balances)}")
    return dict(balances)