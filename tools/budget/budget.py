"""Budget module — data model, persistence, domain services, analytics, and a CLI.

Single source of truth for the budget tool. The agent (agent.py) and the web UI
(../../app.py) both call the service + analytics functions here so the logic lives
in one place. Data is a JSON file next to this script, shared across all three.

Features: categorized transactions, per-category spending limits (percent of income
or fixed), bill due-date reminders, and savings goals with timelines.
"""

import calendar as _calendar
import json
import os
from datetime import date

DATA = os.path.join(os.path.dirname(__file__), "data.json")

DEFAULT = {
    "balance": 0.0,
    "transactions": [],  # {type, amount, category, note, date}
    "income_sources": ["Job", "Business"],  # where 'earned' money is from
    "expense_categories": ["Bills", "Subscriptions", "Fun", "Savings"],  # where 'spent' goes
    "limits": [],  # {category, type: "percent"|"fixed", value}
    "bills": [],   # {name, amount, due_day, category, note}
    "goals": [],   # {name, target, saved, target_date}
}


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def load():
    if not os.path.exists(DATA) or os.path.getsize(DATA) == 0:
        return json.loads(json.dumps(DEFAULT))  # fresh deep copy
    with open(DATA) as f:
        return _migrate(json.load(f))


def save(data):
    with open(DATA, "w") as f:
        json.dump(data, f, indent=2)


def _migrate(data):
    """Fill missing keys and stamp legacy transactions so old files keep working."""
    for key, default in DEFAULT.items():
        if key not in data:
            data[key] = json.loads(json.dumps(default))
    for t in data["transactions"]:
        t.setdefault("category", "Uncategorized")
    return data


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _month(iso=None):
    return (iso or date.today().isoformat())[:7]


def _r(x):
    return round(float(x), 2)


def _num(x):
    """Parse a positive money amount or raise ValueError."""
    v = round(float(x), 2)
    if v <= 0:
        raise ValueError("amount must be positive")
    return v


# --------------------------------------------------------------------------- #
# Service mutations (load -> mutate -> save, return {"ok": ...} envelope)
# --------------------------------------------------------------------------- #
def _register_category(data, kind, category):
    bucket = "income_sources" if kind == "earned" else "expense_categories"
    if category not in data[bucket]:
        data[bucket].append(category)


def add_transaction(kind, amount, category, note=""):
    """Record income ('earned', from a source) or an expense ('spent', to a category)."""
    if kind not in ("earned", "spent"):
        return {"ok": False, "error": "kind must be 'earned' or 'spent'"}
    try:
        amount = _num(amount)
    except (TypeError, ValueError) as e:
        msg = str(e) if "positive" in str(e) else "amount must be a number"
        return {"ok": False, "error": msg}
    category = str(category).strip()
    if not category:
        return {"ok": False, "error": "category is required (earned=from, spent=to)"}
    data = load()
    data["balance"] = _r(data["balance"] + (amount if kind == "earned" else -amount))
    data["transactions"].append(
        {"type": kind, "amount": amount, "category": category,
         "note": str(note), "date": date.today().isoformat()}
    )
    _register_category(data, kind, category)
    save(data)
    return {"ok": True, "balance": data["balance"], "category": category}


def delete_transaction(index):
    """Remove a transaction and recompute the balance from what remains."""
    data = load()
    try:
        removed = data["transactions"].pop(int(index))
    except (IndexError, ValueError, TypeError):
        return {"ok": False, "error": f"no transaction at index {index}"}
    data["balance"] = _r(sum(
        t["amount"] if t["type"] == "earned" else -t["amount"]
        for t in data["transactions"]
    ))
    save(data)
    return {"ok": True, "removed": removed, "balance": data["balance"]}


def add_category(kind, name):
    """Register a spending destination ('spent') or income source ('earned')."""
    if kind not in ("earned", "spent"):
        return {"ok": False, "error": "kind must be 'earned' or 'spent'"}
    name = str(name).strip()
    if not name:
        return {"ok": False, "error": "name is required"}
    data = load()
    _register_category(data, kind, name)
    save(data)
    return {"ok": True, **list_categories(data)}


def list_categories(data=None):
    data = data or load()
    return {"income_sources": data["income_sources"],
            "expense_categories": data["expense_categories"]}


def set_limit(category, type="percent", value=0):
    """Create or update a monthly limit for an expense category (upsert)."""
    if type not in ("percent", "fixed"):
        return {"ok": False, "error": "type must be 'percent' or 'fixed'"}
    try:
        value = round(float(value), 2)
    except (TypeError, ValueError):
        return {"ok": False, "error": "value must be a number"}
    if value < 0:
        return {"ok": False, "error": "value must be >= 0"}
    category = str(category).strip()
    if not category:
        return {"ok": False, "error": "category is required"}
    data = load()
    for lim in data["limits"]:
        if lim["category"].lower() == category.lower():
            lim["type"], lim["value"] = type, value
            break
    else:
        data["limits"].append({"category": category, "type": type, "value": value})
    if category not in data["expense_categories"]:
        data["expense_categories"].append(category)
    save(data)
    return {"ok": True, "limit": {"category": category, "type": type, "value": value}}


def remove_limit(category):
    data = load()
    target = str(category).strip().lower()
    before = len(data["limits"])
    data["limits"] = [l for l in data["limits"] if l["category"].lower() != target]
    if len(data["limits"]) == before:
        return {"ok": False, "error": f"no limit for '{category}'"}
    save(data)
    return {"ok": True, "removed": category}


def add_bill(name, amount, due_day, category="", note=""):
    """Add a recurring bill with a monthly due day (1-31)."""
    name = str(name).strip()
    if not name:
        return {"ok": False, "error": "name is required"}
    try:
        amount = _num(amount)
    except (TypeError, ValueError):
        return {"ok": False, "error": "amount must be a positive number"}
    try:
        due_day = int(due_day)
    except (TypeError, ValueError):
        return {"ok": False, "error": "due_day must be an integer 1-31"}
    if not 1 <= due_day <= 31:
        return {"ok": False, "error": "due_day must be between 1 and 31"}
    data = load()
    bill = {"name": name, "amount": amount, "due_day": due_day,
            "category": str(category).strip(), "note": str(note).strip()}
    data["bills"].append(bill)
    save(data)
    return {"ok": True, "bill": bill, "count": len(data["bills"])}


def remove_bill(index):
    data = load()
    try:
        removed = data["bills"].pop(int(index))
    except (IndexError, ValueError, TypeError):
        return {"ok": False, "error": f"no bill at index {index}"}
    save(data)
    return {"ok": True, "removed": removed}


def add_goal(name, target, target_date="", saved=0):
    """Create a savings goal with a target amount and optional target date."""
    name = str(name).strip()
    if not name:
        return {"ok": False, "error": "name is required"}
    try:
        target = _num(target)
    except (TypeError, ValueError):
        return {"ok": False, "error": "target must be a positive number"}
    try:
        saved = round(float(saved or 0), 2)
    except (TypeError, ValueError):
        return {"ok": False, "error": "saved must be a number"}
    data = load()
    goal = {"name": name, "target": target, "saved": saved,
            "target_date": str(target_date).strip()}
    data["goals"].append(goal)
    save(data)
    return {"ok": True, "goal": goal}


def contribute_goal(index, amount):
    """Add to a goal's saved amount AND log a Savings expense (cash leaves balance)."""
    try:
        amount = _num(amount)
    except (TypeError, ValueError):
        return {"ok": False, "error": "amount must be a positive number"}
    data = load()
    try:
        goal = data["goals"][int(index)]
    except (IndexError, ValueError, TypeError):
        return {"ok": False, "error": f"no goal at index {index}"}
    goal["saved"] = _r(float(goal.get("saved", 0)) + amount)
    save(data)
    # Contribution is real cash moving to savings -> record it as spending.
    tx = add_transaction("spent", amount, "Savings", note=f"goal: {goal['name']}")
    return {"ok": True, "goal": goal, "logged_expense": tx.get("ok", False),
            "balance": tx.get("balance")}


def remove_goal(index):
    data = load()
    try:
        removed = data["goals"].pop(int(index))
    except (IndexError, ValueError, TypeError):
        return {"ok": False, "error": f"no goal at index {index}"}
    save(data)
    return {"ok": True, "removed": removed}


# --------------------------------------------------------------------------- #
# Analytics (pure reads; accept optional data, else load())
# --------------------------------------------------------------------------- #
def overview(data=None):
    data = data or load()
    tx = data["transactions"]
    mth = _month()
    earned = sum(t["amount"] for t in tx if t["type"] == "earned")
    spent = sum(t["amount"] for t in tx if t["type"] == "spent")
    m_earned = sum(t["amount"] for t in tx if t["type"] == "earned" and t["date"][:7] == mth)
    m_spent = sum(t["amount"] for t in tx if t["type"] == "spent" and t["date"][:7] == mth)
    return {
        "balance": _r(data["balance"]),
        "total_earned": _r(earned),
        "total_spent": _r(spent),
        "month": mth,
        "month_earned": _r(m_earned),
        "month_spent": _r(m_spent),
        "month_net": _r(m_earned - m_spent),
        "count": len(tx),
    }


def spending_by_category(data=None, month=None):
    data = data or load()
    month = month or _month()
    out = {}
    for t in data["transactions"]:
        if t["type"] == "spent" and t["date"][:7] == month:
            out[t["category"]] = _r(out.get(t["category"], 0) + t["amount"])
    return out


def income_by_source(data=None, month=None):
    data = data or load()
    month = month or _month()
    out = {}
    for t in data["transactions"]:
        if t["type"] == "earned" and t["date"][:7] == month:
            out[t["category"]] = _r(out.get(t["category"], 0) + t["amount"])
    return out


def monthly_series(data=None, months=6):
    data = data or load()
    today = date.today()
    seq = []
    for i in range(months - 1, -1, -1):
        yy, mm = today.year, today.month - i
        while mm <= 0:
            mm += 12
            yy -= 1
        seq.append(f"{yy:04d}-{mm:02d}")
    agg = {s: {"earned": 0.0, "spent": 0.0} for s in seq}
    for t in data["transactions"]:
        key = t["date"][:7]
        if key in agg:
            agg[key][t["type"]] += t["amount"]
    return [{"month": s, "earned": _r(agg[s]["earned"]), "spent": _r(agg[s]["spent"])}
            for s in seq]


def limit_status(data=None, month=None):
    """Per-limit budget usage. percent limits = value% of this month's income."""
    data = data or load()
    month = month or _month()
    income = sum(t["amount"] for t in data["transactions"]
                 if t["type"] == "earned" and t["date"][:7] == month)
    by_cat = spending_by_category(data, month)
    rows = []
    for lim in data["limits"]:
        allowance = lim["value"] if lim["type"] == "fixed" else _r(lim["value"] / 100.0 * income)
        spent = _r(by_cat.get(lim["category"], 0.0))
        rows.append({
            "category": lim["category"],
            "type": lim["type"],
            "value": lim["value"],
            "allowance": _r(allowance),
            "spent": spent,
            "remaining": _r(allowance - spent),
            "pct": round(spent / allowance * 100, 1) if allowance > 0 else 0.0,
            "over": allowance > 0 and spent > allowance,
        })
    return rows


def _next_due(due_day, today):
    """Next occurrence of a monthly due day on/after today (clamped to month length)."""
    y, m = today.year, today.month
    day = min(due_day, _calendar.monthrange(y, m)[1])
    cand = date(y, m, day)
    if cand < today:
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        cand = date(y, m, min(due_day, _calendar.monthrange(y, m)[1]))
    return cand


def bill_status(data=None, today=None):
    data = data or load()
    today = date.fromisoformat(today) if today else date.today()
    rows = []
    for b in data["bills"]:
        nxt = _next_due(int(b["due_day"]), today)
        days = (nxt - today).days
        rows.append({
            "name": b["name"], "amount": _r(b["amount"]), "due_day": b["due_day"],
            "category": b.get("category", ""), "note": b.get("note", ""),
            "next_due": nxt.isoformat(), "days_until": days,
            "due_soon": days <= 7,
        })
    return sorted(rows, key=lambda r: r["days_until"])


def goal_status(data=None, today=None):
    data = data or load()
    today = date.fromisoformat(today) if today else date.today()
    rows = []
    for g in data["goals"]:
        target = _r(g["target"])
        saved = _r(g.get("saved", 0))
        remaining = _r(max(0.0, target - saved))
        info = {
            "name": g["name"], "target": target, "saved": saved,
            "remaining": remaining,
            "pct": round(min(100.0, saved / target * 100), 1) if target > 0 else 0.0,
            "target_date": g.get("target_date", ""),
            "months_left": None, "per_month": None,
        }
        if g.get("target_date"):
            try:
                td = date.fromisoformat(g["target_date"])
                months = max(0, (td.year - today.year) * 12 + (td.month - today.month))
                info["months_left"] = months
                info["per_month"] = _r(remaining / months) if months > 0 else remaining
            except ValueError:
                pass
        rows.append(info)
    return rows


# --------------------------------------------------------------------------- #
# CLI — basic interactive menu (delegates to the services above)
# --------------------------------------------------------------------------- #
def _ask(prompt):
    return input(prompt).strip()


def _cli_add(kind):
    label = "source (from)" if kind == "earned" else "category (to)"
    amount = _ask("Amount: ")
    category = _ask(f"{label}: ")
    note = _ask("Note: ")
    print(add_transaction(kind, amount, category, note))


def _cli_limits():
    for r in limit_status():
        cap = f"{r['value']}%" if r["type"] == "percent" else f"${r['value']:.2f}"
        flag = "  OVER" if r["over"] else ""
        print(f"  {r['category']}: {cap} -> ${r['allowance']:.2f}  "
              f"spent ${r['spent']:.2f} ({r['pct']}%){flag}")
    if _ask("Add/update a limit? (y/n): ").lower() == "y":
        print(set_limit(_ask("Category: "), _ask("Type (percent/fixed): "),
                        _ask("Value: ")))


def _cli_bills():
    for r in bill_status():
        soon = "  DUE SOON" if r["due_soon"] else ""
        print(f"  {r['name']}: ${r['amount']:.2f}  due {r['next_due']} "
              f"(in {r['days_until']}d){soon}")
    if _ask("Add a bill? (y/n): ").lower() == "y":
        print(add_bill(_ask("Name: "), _ask("Amount: "), _ask("Due day (1-31): "),
                       _ask("Category: "), _ask("Note: ")))


def _cli_goals():
    for i, r in enumerate(goal_status()):
        pace = f"  ${r['per_month']:.2f}/mo" if r["per_month"] else ""
        print(f"  {i}) {r['name']}: ${r['saved']:.2f}/${r['target']:.2f} "
              f"({r['pct']}%){pace}")
    action = _ask("(a)dd goal, (c)ontribute, or enter to skip: ").lower()
    if action == "a":
        print(add_goal(_ask("Name: "), _ask("Target: "),
                       _ask("Target date (YYYY-MM-DD): "), _ask("Already saved: ") or 0))
    elif action == "c":
        print(contribute_goal(_ask("Goal #: "), _ask("Amount: ")))


def _cli_overview():
    o = overview()
    print(f"  Balance: ${o['balance']:.2f}")
    print(f"  This month ({o['month']}): +${o['month_earned']:.2f} "
          f"-${o['month_spent']:.2f}  net ${o['month_net']:.2f}")


def main():
    while True:
        print("\n--- BUDGET ---")
        _cli_overview()
        print("1) Add earned\n2) Add spent\n3) View history\n4) Limits\n"
              "5) Bills\n6) Goals\n0) Quit")
        choice = _ask("> ")
        if choice == "1":
            _cli_add("earned")
        elif choice == "2":
            _cli_add("spent")
        elif choice == "3":
            for t in load()["transactions"]:
                sign = "+" if t["type"] == "earned" else "-"
                print(f"  {t['date']}  {sign}{t['amount']:.2f}  "
                      f"[{t['category']}]  {t['note']}")
        elif choice == "4":
            _cli_limits()
        elif choice == "5":
            _cli_bills()
        elif choice == "6":
            _cli_goals()
        elif choice == "0":
            break
        else:
            print("Unknown option.")


if __name__ == "__main__":
    main()
