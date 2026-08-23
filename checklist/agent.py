"""Checklist agent interface — provider-agnostic capability layer.

Lets any AI agent read, use, and write the checklist module's data. No LLM vendor
lock-in: tools are plain Python with JSON-Schema parameter specs, so a future
assistant can introspect them (`describe()`), decide when to call them
(`when_to_use` + USAGE_RULES), and run them (`execute()`).

Shared shape across all module agents:
- TOOLS          : list of tool specs (name, description, when_to_use, parameters, requires_confirmation)
- USAGE_RULES    : plain-language guidance for the agent
- execute(action, params, confirm)  : run one tool, returns a result dict
- describe()     : full machine-readable capability manifest
- run_cli()      : run a tool from the shell, no LLM needed (see __main__)

Data is shared with checklist.py (the CLI) and app.py (the web UI) via data.json.
"""

import importlib.util
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PRIORITIES = ("low", "med", "high")


def _sibling(filename):
    """Import a sibling module by path (works from any cwd, avoids name clashes)."""
    path = os.path.join(_HERE, filename)
    spec = importlib.util.spec_from_file_location("_store_" + filename[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_store = _sibling("checklist.py")  # reuse its load()/save() and data.json location


# --------------------------------------------------------------------------- #
# Handlers — the actual read/use/write operations
# --------------------------------------------------------------------------- #
def list_items(status="all"):
    """Read: checklist items. status = all | open | done."""
    if status not in ("all", "open", "done"):
        return {"ok": False, "error": "status must be all, open, or done"}
    items = _store.load()
    if status == "open":
        items = [it for it in items if not it["done"]]
    elif status == "done":
        items = [it for it in items if it["done"]]
    return {"ok": True, "items": items}


def add_item(item, description="", priority="med"):
    """Write: add a checklist item (starts not done)."""
    if not str(item).strip():
        return {"ok": False, "error": "item is required"}
    if priority not in _PRIORITIES:
        return {"ok": False, "error": f"priority must be one of {_PRIORITIES}"}
    items = _store.load()
    entry = {"item": str(item).strip(), "description": str(description).strip(),
             "priority": priority, "done": False}
    items.append(entry)
    _store.save(items)
    return {"ok": True, "item": entry, "count": len(items)}


def set_done(index, done=True):
    """Write: mark an item done or reopen it. Non-destructive (reversible)."""
    items = _store.load()
    try:
        items[int(index)]["done"] = bool(done)
    except (IndexError, ValueError, TypeError):
        return {"ok": False, "error": f"no item at index {index}"}
    _store.save(items)
    return {"ok": True, "item": items[int(index)]}


def delete_item(index):
    """Write (destructive): remove an item by index."""
    items = _store.load()
    try:
        removed = items.pop(int(index))
    except (IndexError, ValueError, TypeError):
        return {"ok": False, "error": f"no item at index {index}"}
    _store.save(items)
    return {"ok": True, "removed": removed, "count": len(items)}


# --------------------------------------------------------------------------- #
# Capability manifest
# --------------------------------------------------------------------------- #
USAGE_RULES = """\
CHECKLIST module — a to-do list: item, short description, priority, done flag.

When to use:
- User mentions a task, to-do, "remind me to", "I need to", "mark X done",
  "what's left", or asks what is on their list.
Factors before writing:
- priority is one of low/med/high; default med if unstated.
- Read list_items(status='open') to see remaining work or to find the index to act on.
- Indexes come from list_items() output order.
Confirmations:
- add_item and set_done are safe (set_done is reversible via done=False).
- delete_item is DESTRUCTIVE: prefer set_done for completed tasks; only delete on an
  explicit request, and confirm first, then call confirm=True.
"""

TOOLS = [
    {
        "name": "list_items",
        "description": "List checklist items; filter by all/open/done.",
        "when_to_use": "Seeing outstanding tasks or finding an item to update.",
        "parameters": {
            "type": "object",
            "properties": {"status": {"type": "string",
                                      "enum": ["all", "open", "done"]}},
            "required": [],
        },
        "requires_confirmation": False,
    },
    {
        "name": "add_item",
        "description": "Add a new checklist item.",
        "when_to_use": "User has a new task/to-do.",
        "parameters": {
            "type": "object",
            "properties": {
                "item": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string", "enum": list(_PRIORITIES)},
            },
            "required": ["item"],
        },
        "requires_confirmation": False,
    },
    {
        "name": "set_done",
        "description": "Mark an item done (or reopen with done=false).",
        "when_to_use": "User completes or reopens a task.",
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "minimum": 0},
                "done": {"type": "boolean"},
            },
            "required": ["index"],
        },
        "requires_confirmation": False,
    },
    {
        "name": "delete_item",
        "description": "Remove an item by index.",
        "when_to_use": "User explicitly wants a task deleted (not just completed).",
        "parameters": {
            "type": "object",
            "properties": {"index": {"type": "integer", "minimum": 0}},
            "required": ["index"],
        },
        "requires_confirmation": True,
    },
]

_HANDLERS = {
    "list_items": list_items,
    "add_item": add_item,
    "set_done": set_done,
    "delete_item": delete_item,
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
    return {"module": "checklist", "usage_rules": USAGE_RULES, "tools": TOOLS}


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
