"""Budget agent interface — provider-agnostic capability layer.

Lets any AI agent read, use, and write the budget module's data. No LLM vendor
lock-in: tools are plain Python with JSON-Schema parameter specs, so a future
assistant can introspect them (`describe()`), decide when to call them
(`when_to_use` + USAGE_RULES), and run them (`execute()`).

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
from datetime import date

_HERE = os.path.dirname(os.path.abspath(__file__))


def _sibling(filename):
    """Import a sibling module by path (works from any cwd, avoids name clashes)."""
    path = os.path.join(_HERE, filename)
    spec = importlib.util.spec_from_file_location("_store_" + filename[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_store = _sibling("budget.py")  # reuse its load()/save() and data.json location


# --------------------------------------------------------------------------- #
# Handlers — the actual read/use/write operations
# --------------------------------------------------------------------------- #
def get_summary():
    """Read: current balance plus totals. Cheap, safe, call freely."""
    data = _store.load()
    earned = sum(t["amount"] for t in data["transactions"] if t["type"] == "earned")
    spent = sum(t["amount"] for t in data["transactions"] if t["type"] == "spent")
    return {
        "ok": True,
        "balance": round(data["balance"], 2),
        "total_earned": round(earned, 2),
        "total_spent": round(spent, 2),
        "count": len(data["transactions"]),
    }


def list_transactions(limit=None):
    """Read: transaction history, newest last. `limit` returns only the last N."""
    tx = _store.load()["transactions"]
    if limit is not None:
        tx = tx[-int(limit):]
    return {"ok": True, "transactions": tx}


def add_transaction(kind, amount, note=""):
    """Write: record income or an expense and update the balance."""
    if kind not in ("earned", "spent"):
        return {"ok": False, "error": "kind must be 'earned' or 'spent'"}
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        return {"ok": False, "error": "amount must be a number"}
    if amount <= 0:
        return {"ok": False, "error": "amount must be positive"}
    data = _store.load()
    data["balance"] += amount if kind == "earned" else -amount
    data["transactions"].append(
        {"type": kind, "amount": amount, "note": str(note),
         "date": date.today().isoformat()}
    )
    _store.save(data)
    return {"ok": True, "balance": round(data["balance"], 2)}


def delete_transaction(index):
    """Write (destructive): remove a transaction and recompute the balance."""
    data = _store.load()
    try:
        removed = data["transactions"].pop(int(index))
    except (IndexError, ValueError, TypeError):
        return {"ok": False, "error": f"no transaction at index {index}"}
    data["balance"] = round(
        sum(t["amount"] if t["type"] == "earned" else -t["amount"]
            for t in data["transactions"]),
        2,
    )
    _store.save(data)
    return {"ok": True, "removed": removed, "balance": data["balance"]}


# --------------------------------------------------------------------------- #
# Capability manifest
# --------------------------------------------------------------------------- #
USAGE_RULES = """\
BUDGET module — tracks money: current balance, income (earned), expenses (spent).

When to use:
- User mentions money in/out, income, pay, a purchase, a bill, "how much do I have",
  "what did I spend", budget/balance questions.
Factors before writing:
- amount must be a positive number; classify as 'earned' (money in) or 'spent' (out).
- Prefer reading get_summary()/list_transactions() before answering balance questions;
  do not guess numbers.
Confirmations:
- add_transaction is safe to run once details are clear.
- delete_transaction is DESTRUCTIVE: confirm with the user first, then call with
  confirm=True. It also recomputes the balance from remaining history.
"""

TOOLS = [
    {
        "name": "get_summary",
        "description": "Get current balance and totals for earned/spent.",
        "when_to_use": "Answering any 'how much / balance / totals' question.",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "requires_confirmation": False,
    },
    {
        "name": "list_transactions",
        "description": "List transaction history (optionally only the last N).",
        "when_to_use": "User wants to see or reason over past income/expenses.",
        "parameters": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1,
                                     "description": "Return only the last N."}},
            "required": [],
        },
        "requires_confirmation": False,
    },
    {
        "name": "add_transaction",
        "description": "Record income ('earned') or an expense ('spent').",
        "when_to_use": "User reports money received or spent.",
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["earned", "spent"]},
                "amount": {"type": "number", "exclusiveMinimum": 0},
                "note": {"type": "string"},
            },
            "required": ["kind", "amount"],
        },
        "requires_confirmation": False,
    },
    {
        "name": "delete_transaction",
        "description": "Remove a transaction by index and recompute the balance.",
        "when_to_use": "User asks to remove/undo a specific recorded transaction.",
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
    "add_transaction": add_transaction,
    "delete_transaction": delete_transaction,
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
