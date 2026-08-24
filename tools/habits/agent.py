"""Habits agent interface — provider-agnostic capability layer.

Lets any AI agent read, use, and write the habits module's data. No LLM vendor
lock-in: tools are plain Python with JSON-Schema parameter specs, so a future
assistant can introspect them (`describe()`), decide when to call them
(`when_to_use` + USAGE_RULES), and run them (`execute()`).

Shared shape across all module agents:
- TOOLS          : list of tool specs (name, description, when_to_use, parameters, requires_confirmation)
- USAGE_RULES    : plain-language guidance for the agent
- execute(action, params, confirm)  : run one tool, returns a result dict
- describe()     : full machine-readable capability manifest
- run_cli()      : run a tool from the shell, no LLM needed (see __main__)

Data is shared with habits.py (the CLI) and app.py (the web UI) via data.json.
"""

import importlib.util
import json
import os
import sys
from datetime import date as _date, datetime

_HERE = os.path.dirname(os.path.abspath(__file__))


def _sibling(filename):
    """Import a sibling module by path (works from any cwd, avoids name clashes)."""
    path = os.path.join(_HERE, filename)
    spec = importlib.util.spec_from_file_location("_store_" + filename[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_store = _sibling("habits.py")  # reuse its load()/save() and data.json location


def _find(habits, name):
    """Return the index of a habit by case-insensitive name, or -1."""
    name = str(name).strip().lower()
    for i, h in enumerate(habits):
        if h["name"].lower() == name:
            return i
    return -1


# --------------------------------------------------------------------------- #
# Handlers — the actual read/use/write operations
# --------------------------------------------------------------------------- #
def list_habits():
    """Read: all habits with completion count and last-completed timestamp."""
    habits = _store.load()
    out = [{"name": h["name"], "count": len(h["completions"]),
            "last": h["completions"][-1] if h["completions"] else None}
           for h in habits]
    return {"ok": True, "habits": out}


def add_habit(name):
    """Write: create a new habit (no completions yet)."""
    if not str(name).strip():
        return {"ok": False, "error": "name is required"}
    habits = _store.load()
    if _find(habits, name) != -1:
        return {"ok": False, "error": f"habit '{name}' already exists"}
    habits.append({"name": str(name).strip(), "completions": []})
    _store.save(habits)
    return {"ok": True, "name": str(name).strip(), "count": len(habits)}


def mark_complete(name, timestamp=None):
    """Write: log a completion for a habit. Records date+time (ISO, now if omitted)."""
    habits = _store.load()
    idx = _find(habits, name)
    if idx == -1:
        return {"ok": False, "error": f"no habit named '{name}'"}
    stamp = timestamp or datetime.now().isoformat(timespec="seconds")
    habits[idx]["completions"].append(str(stamp))
    _store.save(habits)
    return {"ok": True, "name": habits[idx]["name"], "completed_at": stamp,
            "count": len(habits[idx]["completions"])}


def delete_habit(name):
    """Write (destructive): remove a habit and its whole completion history."""
    habits = _store.load()
    idx = _find(habits, name)
    if idx == -1:
        return {"ok": False, "error": f"no habit named '{name}'"}
    removed = habits.pop(idx)
    _store.save(habits)
    return {"ok": True, "removed": removed["name"],
            "lost_completions": len(removed["completions"])}


# --------------------------------------------------------------------------- #
# Read-only context + destructive-action preview (the tool owns its own meaning)
# --------------------------------------------------------------------------- #
def context():
    """Read-only summary for the assistant: each habit with total + today's status."""
    today = _date.today().isoformat()
    habits = _store.load()
    if not habits:
        return "No habits tracked."
    lines = ["Habits (name · total · done today?):"]
    for h in habits:
        done_today = today in {str(c)[:10] for c in h.get("completions", [])}
        lines.append(f"  {h['name']}: {len(h.get('completions', []))} done, "
                     f"today {'yes' if done_today else 'no'}")
    return "\n".join(lines)


def _preview(action, params):
    """(exists, label) for a destructive action; label doubles as the not-found error."""
    if action == "delete_habit":
        habits = _store.load()
        n = str(params.get("name", "")).strip()
        if _find(habits, n) != -1:
            return True, f"habit '{n}' and its entire history"
        return False, f"no habit named '{n}'"
    return True, action.replace("_", " ")


# --------------------------------------------------------------------------- #
# Capability manifest
# --------------------------------------------------------------------------- #
USAGE_RULES = """\
HABITS module — recurring habits; each completion is stamped with date + time.

When to use:
- User mentions a daily/recurring routine, "I did X today", "did I do X",
  streaks/consistency, or wants to start tracking a new habit.
Factors before writing:
- Habits are addressed by NAME (case-insensitive), not index.
- mark_complete logs the current date+time by default; pass an ISO timestamp only to
  backfill a past completion.
- Read list_habits() to confirm a habit exists and to report counts / last done.
Confirmations:
- add_habit and mark_complete are safe.
- delete_habit is DESTRUCTIVE: it discards the entire completion history; confirm with
  the user first, then call confirm=True.
"""

TOOLS = [
    {
        "name": "list_habits",
        "description": "List habits with completion count and last-completed time.",
        "when_to_use": "Reporting progress or checking whether a habit exists.",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "requires_confirmation": False,
    },
    {
        "name": "add_habit",
        "mutates": True,
        "description": "Create a new habit to track.",
        "when_to_use": "User wants to start tracking a routine.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        "requires_confirmation": False,
    },
    {
        "name": "mark_complete",
        "mutates": True,
        "description": "Log a completion for a habit (date+time).",
        "when_to_use": "User reports doing a tracked habit.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "timestamp": {"type": "string",
                              "description": "ISO datetime; omit for now."},
            },
            "required": ["name"],
        },
        "requires_confirmation": False,
    },
    {
        "name": "delete_habit",
        "mutates": True,
        "description": "Remove a habit and its full completion history.",
        "when_to_use": "User wants to stop tracking a habit entirely.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        "requires_confirmation": True,
    },
]

_HANDLERS = {
    "list_habits": list_habits,
    "add_habit": add_habit,
    "mark_complete": mark_complete,
    "delete_habit": delete_habit,
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
        exists, label = _preview(action, params)
        if not exists:
            return {"ok": False, "error": label}
        return {"ok": False, "needs_confirmation": True,
                "message": f"Delete {label}? This can't be undone.",
                "params": params}
    try:
        return _HANDLERS[action](**params)
    except TypeError as e:
        return {"ok": False, "error": f"bad parameters for '{action}': {e}"}


def describe():
    """Machine-readable manifest for an agent to introspect this module."""
    return {"module": "habits", "usage_rules": USAGE_RULES, "tools": TOOLS}


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
