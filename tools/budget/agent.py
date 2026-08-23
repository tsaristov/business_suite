"""Budget agent interface — provider-agnostic capability layer.

Lets any AI agent read, use, and write the budget module's data. No LLM vendor
lock-in: tools are plain Python with JSON-Schema parameter specs, so a future
assistant can introspect them (`describe()`), decide when to call them
(`when_to_use` + USAGE_RULES), and run them (`execute()`).

Handlers are thin wrappers over budget.py's service + analytics functions, which are
the single source of truth (also used by the CLI and the web UI).

Shared shape across all module agents:
- TOOLS          : list of tool specs (name, description, when_to_use, parameters, requires_confirmation)
- USAGE_RULES    : plain-language guidance for the agent
- execute(action, params, confirm)  : run one tool, returns a result dict
- describe()     : full machine-readable capability manifest
- run_cli()      : run a tool from the shell, no LLM needed (see __main__)

Data is shared with budget.py (the CLI) and app.py (the web UI) via data.json.
"""

import importlib.util
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIMIT_TYPES = ("percent", "fixed")


def _sibling(filename):
    """Import a sibling module by path (works from any cwd, avoids name clashes)."""
    path = os.path.join(_HERE, filename)
    spec = importlib.util.spec_from_file_location("_store_" + filename[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_store = _sibling("budget.py")  # reuse its services/analytics and data.json location


# --------------------------------------------------------------------------- #
# Handlers — thin wrappers over budget.py (single source of truth)
# --------------------------------------------------------------------------- #
def get_summary():
    """Read: balance, lifetime totals, and this month's earned/spent/net."""
    return {"ok": True, **_store.overview()}


def list_transactions(limit=None):
    """Read: transaction history (optionally only the last N)."""
    tx = _store.load()["transactions"]
    if limit is not None:
        tx = tx[-int(limit):]
    return {"ok": True, "transactions": tx}


def list_categories():
    """Read: known income sources and expense categories."""
    return {"ok": True, **_store.list_categories()}


def limit_status():
    """Read: each spending limit's allowance, spent, remaining, and over-flag."""
    return {"ok": True, "limits": _store.limit_status()}


def bill_status():
    """Read: bills sorted by next due date, with days-until and due-soon flags."""
    return {"ok": True, "bills": _store.bill_status()}


def goal_status():
    """Read: goal progress with remaining, months left, and per-month pace."""
    return {"ok": True, "goals": _store.goal_status()}


def spending_breakdown(month=None):
    """Read: spending by category (a month) plus 6-month income-vs-spending series."""
    return {"ok": True,
            "by_category": _store.spending_by_category(month=month),
            "income_by_source": _store.income_by_source(month=month),
            "series": _store.monthly_series()}


# Writes delegate straight to the service layer (already return {"ok": ...}).
def add_transaction(kind, amount, category, note=""):
    return _store.add_transaction(kind, amount, category, note)


def add_category(kind, name):
    return _store.add_category(kind, name)


def set_limit(category, type="percent", value=0):
    return _store.set_limit(category, type, value)


def add_bill(name, amount, due_day, category="", note=""):
    return _store.add_bill(name, amount, due_day, category, note)


def add_goal(name, target, target_date="", saved=0):
    return _store.add_goal(name, target, target_date, saved)


def contribute_goal(index, amount):
    return _store.contribute_goal(index, amount)


def delete_transaction(index):
    return _store.delete_transaction(index)


def remove_limit(category):
    return _store.remove_limit(category)


def remove_bill(index):
    return _store.remove_bill(index)


def remove_goal(index):
    return _store.remove_goal(index)


# --------------------------------------------------------------------------- #
# Capability manifest
# --------------------------------------------------------------------------- #
USAGE_RULES = """\
BUDGET module — money with categorization, spending limits, bill reminders, and goals.

When to use:
- Any money in/out, income, pay, purchase, bill, balance/"how much" question, or a
  request about budgets, upcoming bills, or savings goals.

Categorization (REQUIRED on every transaction):
- 'earned' needs a SOURCE (where it's from): e.g. Job, Business, Gift.
- 'spent' needs a CATEGORY (where it's going): e.g. Bills, Subscriptions, Fun, Savings.
- Call list_categories() to reuse existing names; new ones are auto-registered.

Spending limits (automatic budgeting):
- A limit is per expense category, either 'percent' (of THIS month's income, so it
  auto-scales with earnings) or 'fixed' (a flat monthly $ cap).
- Read limit_status() before advising on discretionary spend; warn when 'over' is true.

Bills:
- add_bill stores a monthly due_day (1-31). bill_status() gives the next due date and
  days_until; proactively remind when due_soon is true.

Goals:
- add_goal takes a target and optional target_date; goal_status() reports pace needed.
- contribute_goal ALSO logs a 'spent' transaction in 'Savings' and lowers the balance
  (a contribution is real cash leaving liquid funds) — tell the user both happened.

Confirmations:
- Reads and adds/updates are safe once details are clear.
- delete_transaction, remove_limit, remove_bill, remove_goal are DESTRUCTIVE: confirm
  with the user first, then call with confirm=True.
"""

TOOLS = [
    {
        "name": "get_summary",
        "description": "Balance, lifetime totals, and this month's earned/spent/net.",
        "when_to_use": "Any 'how much / balance / this month' question.",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "requires_confirmation": False,
    },
    {
        "name": "list_transactions",
        "description": "List transaction history (optionally only the last N).",
        "when_to_use": "Reviewing or reasoning over past income/expenses.",
        "parameters": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1}},
            "required": [],
        },
        "requires_confirmation": False,
    },
    {
        "name": "list_categories",
        "description": "List known income sources and expense categories.",
        "when_to_use": "Before adding a transaction, to reuse an existing category.",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "requires_confirmation": False,
    },
    {
        "name": "limit_status",
        "description": "Per-category budget usage vs allowance (percent or fixed).",
        "when_to_use": "Advising on spending or checking if a category is over budget.",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "requires_confirmation": False,
    },
    {
        "name": "bill_status",
        "description": "Bills sorted by next due date, with days-until / due-soon.",
        "when_to_use": "Reminding about upcoming or overdue bills.",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "requires_confirmation": False,
    },
    {
        "name": "goal_status",
        "description": "Savings-goal progress, remaining, months left, monthly pace.",
        "when_to_use": "Reporting on goals or the pace needed to hit them.",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "requires_confirmation": False,
    },
    {
        "name": "spending_breakdown",
        "description": "Spending by category + 6-month income/spend series (for charts).",
        "when_to_use": "Explaining where money went or trends over months.",
        "parameters": {
            "type": "object",
            "properties": {"month": {"type": "string", "description": "YYYY-MM"}},
            "required": [],
        },
        "requires_confirmation": False,
    },
    {
        "name": "add_transaction",
        "description": "Record income ('earned') or an expense ('spent'). Category required.",
        "when_to_use": "User reports money received or spent.",
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["earned", "spent"]},
                "amount": {"type": "number", "exclusiveMinimum": 0},
                "category": {"type": "string",
                             "description": "earned=source (from), spent=category (to)"},
                "note": {"type": "string"},
            },
            "required": ["kind", "amount", "category"],
        },
        "requires_confirmation": False,
    },
    {
        "name": "add_category",
        "description": "Register a new income source or expense category.",
        "when_to_use": "User introduces a category that doesn't exist yet.",
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["earned", "spent"]},
                "name": {"type": "string"},
            },
            "required": ["kind", "name"],
        },
        "requires_confirmation": False,
    },
    {
        "name": "set_limit",
        "description": "Create/update a monthly limit for a category (percent or fixed).",
        "when_to_use": "User sets a budget for a category.",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "type": {"type": "string", "enum": list(_LIMIT_TYPES),
                         "description": "percent = % of this month's income; fixed = $ cap"},
                "value": {"type": "number", "minimum": 0},
            },
            "required": ["category", "type", "value"],
        },
        "requires_confirmation": False,
    },
    {
        "name": "add_bill",
        "description": "Add a recurring bill with a monthly due day (1-31).",
        "when_to_use": "User has a recurring bill to be reminded about.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "amount": {"type": "number", "exclusiveMinimum": 0},
                "due_day": {"type": "integer", "minimum": 1, "maximum": 31},
                "category": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["name", "amount", "due_day"],
        },
        "requires_confirmation": False,
    },
    {
        "name": "add_goal",
        "description": "Create a savings goal with a target and optional target date.",
        "when_to_use": "User wants to save toward something.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "target": {"type": "number", "exclusiveMinimum": 0},
                "target_date": {"type": "string", "description": "YYYY-MM-DD"},
                "saved": {"type": "number", "minimum": 0},
            },
            "required": ["name", "target"],
        },
        "requires_confirmation": False,
    },
    {
        "name": "contribute_goal",
        "description": "Add to a goal's saved amount; also logs a Savings expense.",
        "when_to_use": "User puts money toward a goal.",
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "minimum": 0},
                "amount": {"type": "number", "exclusiveMinimum": 0},
            },
            "required": ["index", "amount"],
        },
        "requires_confirmation": False,
    },
    {
        "name": "delete_transaction",
        "description": "Remove a transaction by index and recompute the balance.",
        "when_to_use": "User wants to undo a recorded transaction.",
        "parameters": {
            "type": "object",
            "properties": {"index": {"type": "integer", "minimum": 0}},
            "required": ["index"],
        },
        "requires_confirmation": True,
    },
    {
        "name": "remove_limit",
        "description": "Delete a category's spending limit.",
        "when_to_use": "User no longer wants to budget a category.",
        "parameters": {
            "type": "object",
            "properties": {"category": {"type": "string"}},
            "required": ["category"],
        },
        "requires_confirmation": True,
    },
    {
        "name": "remove_bill",
        "description": "Delete a bill by index.",
        "when_to_use": "A bill no longer applies.",
        "parameters": {
            "type": "object",
            "properties": {"index": {"type": "integer", "minimum": 0}},
            "required": ["index"],
        },
        "requires_confirmation": True,
    },
    {
        "name": "remove_goal",
        "description": "Delete a savings goal by index.",
        "when_to_use": "User abandons or completes a goal.",
        "parameters": {
            "type": "object",
            "properties": {"index": {"type": "integer", "minimum": 0}},
            "required": ["index"],
        },
        "requires_confirmation": True,
    },
]

_HANDLERS = {
    "get_summary": get_summary,
    "list_transactions": list_transactions,
    "list_categories": list_categories,
    "limit_status": limit_status,
    "bill_status": bill_status,
    "goal_status": goal_status,
    "spending_breakdown": spending_breakdown,
    "add_transaction": add_transaction,
    "add_category": add_category,
    "set_limit": set_limit,
    "add_bill": add_bill,
    "add_goal": add_goal,
    "contribute_goal": contribute_goal,
    "delete_transaction": delete_transaction,
    "remove_limit": remove_limit,
    "remove_bill": remove_bill,
    "remove_goal": remove_goal,
}


# --------------------------------------------------------------------------- #
# Generic runner helpers (used by any agent / the assistant / the shell)
# --------------------------------------------------------------------------- #
def _spec(action):
    return next((t for t in TOOLS if t["name"] == action), None)


def execute(action, params=None, confirm=False):
    """Run a tool by name. Returns a result dict; never raises for normal errors.

    Destructive tools (requires_confirmation) return {"needs_confirmation": True}
    until called again with confirm=True.
    """
    params = params or {}
    spec = _spec(action)
    if spec is None:
        return {"ok": False, "error": f"unknown action '{action}'",
                "available": list(_HANDLERS)}
    if spec["requires_confirmation"] and not confirm:
        return {"ok": False, "needs_confirmation": True,
                "message": f"'{action}' is destructive. Re-run with confirm=True.",
                "params": params}
    try:
        return _HANDLERS[action](**params)
    except TypeError as e:
        return {"ok": False, "error": f"bad parameters for '{action}': {e}"}


def describe():
    """Machine-readable manifest for an agent to introspect this module."""
    return {"module": "budget", "usage_rules": USAGE_RULES, "tools": TOOLS}


def run_cli(argv):
    """Shell entry: `python agent.py <action|describe> [key=value ...] [--confirm]`."""
    if not argv or argv[0] in ("describe", "-h", "--help"):
        print(json.dumps(describe(), indent=2))
        return
    action, confirm, params = argv[0], False, {}
    for arg in argv[1:]:
        if arg == "--confirm":
            confirm = True
        elif "=" in arg:
            k, v = arg.split("=", 1)
            params[k] = _coerce(v)
    print(json.dumps(execute(action, params, confirm=confirm), indent=2))


def _coerce(v):
    """Best-effort string -> int/float/bool for shell args."""
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


if __name__ == "__main__":
    run_cli(sys.argv[1:])
