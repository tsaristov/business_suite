"""Calendar agent interface — provider-agnostic capability layer.

Lets any AI agent read, use, and write the calendar module's data. No LLM vendor
lock-in: tools are plain Python with JSON-Schema parameter specs, so a future
assistant can introspect them (`describe()`), decide when to call them
(`when_to_use` + USAGE_RULES), and run them (`execute()`).

Shared shape across all module agents:
- TOOLS          : list of tool specs (name, description, when_to_use, parameters, requires_confirmation)
- USAGE_RULES    : plain-language guidance for the agent
- execute(action, params, confirm)  : run one tool, returns a result dict
- describe()     : full machine-readable capability manifest
- run_cli()      : run a tool from the shell, no LLM needed (see __main__)

Data is shared with calendar.py (the CLI) and app.py (the web UI) via data.json.
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


_store = _sibling("calendar.py")  # reuse its load()/save() and data.json location


# --------------------------------------------------------------------------- #
# Handlers — the actual read/use/write operations
# --------------------------------------------------------------------------- #
def list_events(date=None):
    """Read: events sorted by date. Optional exact-date filter (YYYY-MM-DD)."""
    events = sorted(_store.load(), key=lambda e: e.get("date", ""))
    if date:
        events = [e for e in events if e.get("date") == date]
    return {"ok": True, "events": events}


def add_event(title, date, description="", priority="med", duration="", time=""):
    """Write: create an event. `date` is YYYY-MM-DD; `time` is HH:MM (blank = all-day);
    `duration` is free text (e.g. 2h)."""
    if not str(title).strip():
        return {"ok": False, "error": "title is required"}
    if priority not in _PRIORITIES:
        return {"ok": False, "error": f"priority must be one of {_PRIORITIES}"}
    events = _store.load()
    event = {"title": str(title).strip(), "description": str(description).strip(),
             "priority": priority, "date": str(date).strip(),
             "time": str(time).strip(), "duration": str(duration).strip()}
    events.append(event)
    _store.save(events)
    return {"ok": True, "event": event, "count": len(events)}


def update_event(index, **fields):
    """Write: change fields of an existing event by index."""
    events = _store.load()
    try:
        event = events[int(index)]
    except (IndexError, ValueError, TypeError):
        return {"ok": False, "error": f"no event at index {index}"}
    allowed = {"title", "description", "priority", "date", "time", "duration"}
    bad = set(fields) - allowed
    if bad:
        return {"ok": False, "error": f"unknown fields: {sorted(bad)}"}
    if "priority" in fields and fields["priority"] not in _PRIORITIES:
        return {"ok": False, "error": f"priority must be one of {_PRIORITIES}"}
    event.update({k: str(v) for k, v in fields.items()})
    _store.save(events)
    return {"ok": True, "event": event}


def delete_event(index):
    """Write (destructive): remove an event by index."""
    events = _store.load()
    try:
        removed = events.pop(int(index))
    except (IndexError, ValueError, TypeError):
        return {"ok": False, "error": f"no event at index {index}"}
    _store.save(events)
    return {"ok": True, "removed": removed, "count": len(events)}


# --------------------------------------------------------------------------- #
# Capability manifest
# --------------------------------------------------------------------------- #
USAGE_RULES = """\
CALENDAR module — tracks events: title, description, priority, date, duration.

When to use:
- User mentions scheduling, an appointment, meeting, deadline, "what's on my day",
  "am I free", or asks to add/move/cancel something dated.
Factors before writing:
- Resolve relative dates ("tomorrow", "next Fri", "today") to an absolute YYYY-MM-DD
  before calling add_event/update_event (use the current date from the context).
- ALWAYS pass `time` when the user gives a clock time, converted to 24-hour HH:MM:
  "4:38pm" -> "16:38", "9am" -> "09:00", "noon" -> "12:00". Leave `time` blank only for
  genuinely all-day events.
- priority is one of low/med/high; default med if unstated.
- Read list_events() first to check conflicts or find the item to edit/delete.
- Indexes come from list_events() output order (sorted by date).
Confirmations:
- add_event / update_event are safe once details are confirmed.
- delete_event is DESTRUCTIVE: confirm with the user first, then call confirm=True.
"""

TOOLS = [
    {
        "name": "list_events",
        "description": "List events sorted by date, optionally filtered to one date.",
        "when_to_use": "Checking the schedule, conflicts, or finding an event to edit.",
        "parameters": {
            "type": "object",
            "properties": {"date": {"type": "string",
                                    "description": "Filter to YYYY-MM-DD."}},
            "required": [],
        },
        "requires_confirmation": False,
    },
    {
        "name": "add_event",
        "description": "Create a calendar event.",
        "when_to_use": "User wants to schedule something.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "time": {"type": "string", "description": "24-hour HH:MM (convert '4:38pm'->'16:38'); blank = all-day"},
                "description": {"type": "string"},
                "priority": {"type": "string", "enum": list(_PRIORITIES)},
                "duration": {"type": "string", "description": "Free text, e.g. 2h"},
            },
            "required": ["title", "date"],
        },
        "requires_confirmation": False,
    },
    {
        "name": "update_event",
        "description": "Update fields of an existing event by index.",
        "when_to_use": "User reschedules or edits an event's details.",
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "minimum": 0},
                "title": {"type": "string"},
                "date": {"type": "string"},
                "time": {"type": "string", "description": "24-hour HH:MM (convert '4:38pm'->'16:38'); blank = all-day"},
                "description": {"type": "string"},
                "priority": {"type": "string", "enum": list(_PRIORITIES)},
                "duration": {"type": "string"},
            },
            "required": ["index"],
        },
        "requires_confirmation": False,
    },
    {
        "name": "delete_event",
        "description": "Remove an event by index.",
        "when_to_use": "User cancels an event.",
        "parameters": {
            "type": "object",
            "properties": {"index": {"type": "integer", "minimum": 0}},
            "required": ["index"],
        },
        "requires_confirmation": True,
    },
]

_HANDLERS = {
    "list_events": list_events,
    "add_event": add_event,
    "update_event": update_event,
    "delete_event": delete_event,
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
    return {"module": "calendar", "usage_rules": USAGE_RULES, "tools": TOOLS}


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
