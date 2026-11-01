from collections import defaultdict

from models import Expense, ExpenseItem, GroupMember, ItemShare, Settlement, db


def _lucky(candidates, poster_id, payer_id):
    """Who absorbs leftover cents: poster, else payer, else lowest user id."""
    if poster_id in candidates:
        return poster_id
    if payer_id in candidates:
        return payer_id
    return min(candidates)


def allocate(amount_cents, weights, poster_id, payer_id):
    """Split amount_cents in proportion to weights, rounding each share DOWN.
    Equal weights = even split. Leftover cents go to _lucky()."""
    total_weight = sum(weights.values())
    shares = {u: amount_cents * w // total_weight for u, w in weights.items()}
    leftover = amount_cents - sum(shares.values())
    shares[_lucky(shares, poster_id, payer_id)] += leftover
    return shares


def expense_shares(expense, items):
    """items: list of (amount_cents, [user_ids]). Returns {user_id: cents owed}."""
    poster, payer = expense.created_by, expense.paid_by

    subtotals = defaultdict(int)
    for amount_cents, sharers in items:
        for u, c in allocate(amount_cents, {u: 1 for u in sharers}, poster, payer).items():
            subtotals[u] += c
    subtotals = dict(subtotals)

    shares = dict(subtotals)
    if expense.tax_cents:
        for u, c in allocate(expense.tax_cents, subtotals, poster, payer).items():
            shares[u] += c
    if expense.tip_cents:
        weights = subtotals if expense.tip_mode == "proportional" else {u: 1 for u in subtotals}
        for u, c in allocate(expense.tip_cents, weights, poster, payer).items():
            shares[u] += c
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
        items = []
        for item in db.session.execute(
            db.select(ExpenseItem).where(ExpenseItem.expense_id == exp.id)
        ).scalars():
            sharers = db.session.execute(
                db.select(ItemShare.user_id).where(ItemShare.item_id == item.id)
            ).scalars().all()
            items.append((item.amount_cents, sharers))

        total = sum(a for a, _ in items) + exp.tax_cents + exp.tip_cents
        balances[exp.paid_by] += total
        for uid, cents in expense_shares(exp, items).items():
            balances[uid] -= cents

    for s in db.session.execute(
        db.select(Settlement).where(
            Settlement.group_id == group_id, Settlement.status == "confirmed"
        )
    ).scalars():
        balances[s.from_user] += s.amount_cents
        balances[s.to_user] -= s.amount_cents

    total = sum(balances.values())
    if total != 0:
        raise RuntimeError(f"Balances sum to {total}, not 0: {dict(balances)}")
    return dict(balances)