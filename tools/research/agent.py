"""Research agent interface — provider-agnostic capability layer.

Lets the assistant run web research and produce a downloadable PDF report plus a short
summary. Same shape as the other tool agents (see tools/checklist/agent.py).

Data (report metadata + PDFs) is shared with app.py via research.py.
"""

import importlib.util
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _sibling(filename):
    path = os.path.join(_HERE, filename)
    spec = importlib.util.spec_from_file_location("_store_" + filename[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_store = _sibling("research.py")


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #
def run_research(topic, max_sources=5):
    """Write: research a topic on the web, save a PDF report, return a summary."""
    return _store.run_research(topic, max_sources)


def list_reports():
    """Read: past research reports (newest first)."""
    return _store.list_reports()


# --------------------------------------------------------------------------- #
# Read-only context
# --------------------------------------------------------------------------- #
def context():
    """Recent report titles so the model knows prior research exists (cheap, no network)."""
    reports = _store.load()
    if not reports:
        return None
    recent = ", ".join(r.get("topic", "?") for r in reports[:5])
    return f"{len(reports)} saved research report(s). Recent: {recent}."


# --------------------------------------------------------------------------- #
# Capability manifest
# --------------------------------------------------------------------------- #
USAGE_RULES = """\
RESEARCH module — performs basic web research and writes a PDF report + short summary.

When to use:
- The user asks you to research / look up / investigate a topic, or to "write a report"
  or "make a report" on something. Call run_research with a clear topic string.
Factors:
- run_research is slow (it searches the web, reads pages, and writes a PDF). Call it once
  per request; report the returned summary and mention the PDF is available on the
  Research tab.
- Needs internet access and a running Ollama model.
Confirmations:
- Safe: it only creates a new report, never deletes anything.
"""

TOOLS = [
    {
        "name": "run_research",
        "mutates": True,
        "description": "Research a topic on the web and generate a downloadable PDF "
                       "report plus a short summary.",
        "when_to_use": "User asks to research a topic or write/make a report on something.",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "What to research."},
                "max_sources": {"type": "integer", "minimum": 1, "maximum": 10,
                                "description": "How many web sources to use (default 5)."},
            },
            "required": ["topic"],
        },
        "requires_confirmation": False,
    },
    {
        "name": "list_reports",
        "description": "List past research reports.",
        "when_to_use": "User asks what research/reports exist.",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "requires_confirmation": False,
    },
]

_HANDLERS = {
    "run_research": run_research,
    "list_reports": list_reports,
}


# --------------------------------------------------------------------------- #
# Generic runner
# --------------------------------------------------------------------------- #
def _spec(action):
    return next((t for t in TOOLS if t["name"] == action), None)


def execute(action, params=None, confirm=False):
    params = params or {}
    spec = _spec(action)
    if spec is None:
        return {"ok": False, "error": f"unknown action '{action}'",
                "available": list(_HANDLERS)}
    try:
        return _HANDLERS[action](**params)
    except TypeError as e:
        return {"ok": False, "error": f"bad parameters for '{action}': {e}"}


def describe():
    return {"module": "research", "usage_rules": USAGE_RULES, "tools": TOOLS}


def _coerce(v):
    try:
        return int(v)
    except ValueError:
        return v


def run_cli(argv):
    """Shell entry: `python agent.py <action|describe> [key=value ...]`."""
    if not argv or argv[0] in ("describe", "-h", "--help"):
        print(json.dumps(describe(), indent=2))
        return
    action, params = argv[0], {}
    for arg in argv[1:]:
        if "=" in arg:
            k, v = arg.split("=", 1)
            params[k] = _coerce(v)
    print(json.dumps(execute(action, params), indent=2))


if __name__ == "__main__":
    run_cli(sys.argv[1:])
