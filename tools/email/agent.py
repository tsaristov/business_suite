"""Email agent interface — provider-agnostic capability layer (read-only).

Lets the assistant see and read email across the user's configured accounts. Account
setup (which needs a password field) is UI-only — not exposed as an LLM tool — so the
assistant can only read, never add/remove accounts or send mail.

Same shape as the other tool agents (see tools/checklist/agent.py). Domain logic +
data.json live in mailbox.py (named to avoid shadowing the stdlib `email` package).
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


_store = _sibling("mailbox.py")


# --------------------------------------------------------------------------- #
# Handlers (read-only)
# --------------------------------------------------------------------------- #
def list_accounts():
    """Read: configured email accounts (labels/addresses, no secrets)."""
    return _store.list_accounts()


def list_emails(account, folder="INBOX", limit=20, unread_only=False):
    """Read: recent messages for an account (header summaries, newest first)."""
    return _store.list_emails(account, folder=folder, limit=limit, unread_only=unread_only)


def read_email(account, uid, folder="INBOX"):
    """Read: one message's full body by UID."""
    return _store.read_email(account, uid, folder=folder)


def search_emails(account, query, folder="INBOX", limit=20):
    """Read: full-text search within an account."""
    return _store.search_emails(account, query, folder=folder, limit=limit)


# --------------------------------------------------------------------------- #
# Read-only context
# --------------------------------------------------------------------------- #
def context():
    """Which accounts exist, by label (no network — connecting is deferred to a call)."""
    accts = _store.load().get("accounts", [])
    if not accts:
        return None
    labels = ", ".join(f"{a['label']} ({a['username']})" for a in accts)
    return f"Email accounts configured: {labels}. Use list_emails/read_email to view mail."


# --------------------------------------------------------------------------- #
# Capability manifest
# --------------------------------------------------------------------------- #
USAGE_RULES = """\
EMAIL module — read-only access to the user's configured IMAP accounts.

When to use:
- The user asks to check, see, read, or search their email/inbox/messages.
Factors:
- First call list_accounts() (or use the context) to get the exact `account` label; pass
  that label to the other tools. Never guess a label.
- list_emails returns header summaries with a `uid`; pass that uid to read_email to see
  the full body. Use unread_only=true for "unread"/"new" email questions.
- Accounts are added by the user in the Email tab (needs a password). You cannot add,
  remove, or send email — only read.
Confirmations:
- All email tools are read-only and safe.
"""

TOOLS = [
    {
        "name": "list_accounts",
        "description": "List the user's configured email accounts (labels + addresses).",
        "when_to_use": "To find the account label before listing/reading mail.",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "requires_confirmation": False,
    },
    {
        "name": "list_emails",
        "description": "List recent messages for an account as header summaries "
                       "(subject/from/date/uid/unread), newest first.",
        "when_to_use": "User wants to see their inbox / recent or unread mail.",
        "parameters": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Account label."},
                "folder": {"type": "string", "description": "Mailbox (default INBOX)."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "unread_only": {"type": "boolean"},
            },
            "required": ["account"],
        },
        "requires_confirmation": False,
    },
    {
        "name": "read_email",
        "description": "Read one message's full body by its UID.",
        "when_to_use": "User wants the contents of a specific message from a listing.",
        "parameters": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "uid": {"type": "string", "description": "UID from list_emails/search_emails."},
                "folder": {"type": "string"},
            },
            "required": ["account", "uid"],
        },
        "requires_confirmation": False,
    },
    {
        "name": "search_emails",
        "description": "Full-text search an account's mailbox; returns header summaries.",
        "when_to_use": "User asks for emails about/from a topic or person.",
        "parameters": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "query": {"type": "string"},
                "folder": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["account", "query"],
        },
        "requires_confirmation": False,
    },
]

_HANDLERS = {
    "list_accounts": list_accounts,
    "list_emails": list_emails,
    "read_email": read_email,
    "search_emails": search_emails,
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
    return {"module": "email", "usage_rules": USAGE_RULES, "tools": TOOLS}


def _coerce(v):
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
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
