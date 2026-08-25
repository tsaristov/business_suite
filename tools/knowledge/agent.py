"""Knowledge agent interface — provider-agnostic capability layer for the RAG store.

Exposes the knowledge base as tools the assistant can call: a semantic `search_knowledge`
and a `list_documents`. Ingestion/sync is driven from the Settings tab (see app.py), not
by the LLM, so this layer stays read-only for the assistant.

Same shape as the other tool agents (see tools/checklist/agent.py):
  TOOLS, USAGE_RULES, execute(action, params, confirm), describe(), context(), run_cli().
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


_store = _sibling("knowledge.py")


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #
def search_knowledge(query, top_k=None):
    """Read: semantic search over the user's uploaded documents."""
    return _store.search(query, top_k)


def list_documents():
    """Read: list indexed documents and last-sync info."""
    return _store.list_documents()


# --------------------------------------------------------------------------- #
# Read-only context
# --------------------------------------------------------------------------- #
def context():
    """Tell the model the knowledge base exists and what's in it (names only, no I/O
    into the vector store)."""
    try:
        docs = _store._doc_entries()
    except Exception:
        return None
    if not docs:
        return None
    names = ", ".join(d["name"] for d in docs[:20])
    more = "" if len(docs) <= 20 else f" (+{len(docs) - 20} more)"
    return (f"{len(docs)} document(s) available: {names}{more}. "
            "Call search_knowledge to answer questions about them.")


# --------------------------------------------------------------------------- #
# Capability manifest
# --------------------------------------------------------------------------- #
USAGE_RULES = """\
KNOWLEDGE module — a searchable base of documents the user uploaded (company info,
notes, references, PDFs, etc.).

When to use:
- The user asks a factual question about their own material, company, policies,
  documents, or anything that would be found in uploaded files rather than general
  knowledge. Call search_knowledge with a focused query.
Factors:
- Prefer a specific query over the raw question; you may search more than once.
- If results are empty or irrelevant, say the knowledge base doesn't cover it instead
  of guessing. Do not fabricate sources.
Confirmations:
- All knowledge tools are read-only and safe.
"""

TOOLS = [
    {
        "name": "search_knowledge",
        "description": "Semantic search over the user's uploaded documents; returns the "
                       "most relevant passages with their source file.",
        "when_to_use": "The user asks about their own docs, company, notes, or uploaded "
                       "reference material.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look up."},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 10,
                          "description": "How many passages to retrieve (default from settings)."},
            },
            "required": ["query"],
        },
        "requires_confirmation": False,
    },
    {
        "name": "list_documents",
        "description": "List the documents currently in the knowledge base.",
        "when_to_use": "The user asks what documents/files are available or indexed.",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "requires_confirmation": False,
    },
]

_HANDLERS = {
    "search_knowledge": search_knowledge,
    "list_documents": list_documents,
}


# --------------------------------------------------------------------------- #
# Generic runner
# --------------------------------------------------------------------------- #
def _spec(action):
    return next((t for t in TOOLS if t["name"] == action), None)


def execute(action, params=None, confirm=False):
    """Run a tool by name. Returns a result dict; never raises for normal errors."""
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
    return {"module": "knowledge", "usage_rules": USAGE_RULES, "tools": TOOLS}


def _coerce(v):
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


def run_cli(argv):
    """Shell entry: `python agent.py <action|describe|sync> [key=value ...]`."""
    if not argv or argv[0] in ("describe", "-h", "--help"):
        print(json.dumps(describe(), indent=2))
        return
    if argv[0] == "sync":
        print(json.dumps(_store.sync(), indent=2))
        return
    action, params = argv[0], {}
    for arg in argv[1:]:
        if "=" in arg:
            k, v = arg.split("=", 1)
            params[k] = _coerce(v)
    print(json.dumps(execute(action, params), indent=2))


if __name__ == "__main__":
    run_cli(sys.argv[1:])
