"""Assistant engine — the suite's natural-language secretary.

Provider-agnostic planner that sits on top of the four tool modules (budget,
calendar, checklist, habits) and drives them through their agent.py capability
layers. The shipped model runtime is a LOCAL Ollama model; it is isolated behind
one thin adapter (`_model_chat`) so any provider could replace it.

Design (see output.md):
- "The model plans, deterministic tools act." The LLM only *chooses* tools; every
  mutation runs through a module's agent.execute(), which validates params and gates
  destructive actions behind a confirmation.
- Two-stage routing keeps the tool count per model call small (accurate on 3-4B):
    stage 1 -> pick the module   (4 choices)
    stage 2 -> pick that module's action (<=17 choices)
- Grounding (RAG): a compact, capped snapshot of all four stores is read fresh each
  turn so the model can answer factual questions without holding the whole database.
- Confirmation is deterministic (a yes/no word list), never judged by the model.

Public API (used by app.py):
    chat(message)   -> {"reply", "action", "results"}
    history(limit)  -> {"history": [{role, message, created_at}, ...]}
    clear_history() -> {"ok": True}
"""

import importlib.util
import json
import os
import re
from datetime import datetime, date

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_HERE)
_TOOLS_DIR = os.path.join(_BASE, "tools")
_DATA = os.path.join(_HERE, "data.json")

# Default to a clean, non-reasoning tool-caller. Reasoning models (qwen3.x) dump
# chain-of-thought into the reply and hesitate to call destructive tools, which breaks
# both TTS output and the confirmation gate. Override with OLLAMA_MODEL if desired.
MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")

# Confirmation follow-ups are only honored for a short window.
_PENDING_TTL_SECONDS = 5 * 60

_AFFIRM = {"yes", "y", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm",
           "confirmed", "do it", "go ahead", "please do", "affirmative"}
_DENY = {"no", "n", "nope", "nah", "cancel", "never mind", "nevermind", "stop",
         "don't", "dont", "negative"}

_HELP = ("I can manage your budget, calendar, checklist, and habits. Try "
         "\"what's my balance\", \"schedule a call Friday 3pm\", \"add buy milk "
         "to my checklist\", or \"I did my workout today\".")


# --------------------------------------------------------------------------- #
# Module loading — reuse the suite's file-path import pattern
# --------------------------------------------------------------------------- #
def _load(name, *parts):
    path = os.path.join(_TOOLS_DIR, *parts)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Each value is a module agent exposing describe()/execute() (see tools/*/agent.py).
MODULES = {
    "budget": _load("assistant_budget_agent", "budget", "agent.py"),
    "calendar": _load("assistant_calendar_agent", "calendar", "agent.py"),
    "checklist": _load("assistant_checklist_agent", "checklist", "agent.py"),
    "habits": _load("assistant_habits_agent", "habits", "agent.py"),
}


# --------------------------------------------------------------------------- #
# Store (conversation history + single-slot pending confirmation)
# --------------------------------------------------------------------------- #
def _load_store():
    if not os.path.exists(_DATA) or os.path.getsize(_DATA) == 0:
        return {"history": [], "pending": None}
    with open(_DATA) as f:
        data = json.load(f)
    data.setdefault("history", [])
    data.setdefault("pending", None)
    return data


def _save_store(data):
    with open(_DATA, "w") as f:
        json.dump(data, f, indent=2)


def _record(store, role, message):
    store["history"].append({
        "role": role, "message": message,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    })


# --------------------------------------------------------------------------- #
# Ollama adapter (the only provider-specific code)
# --------------------------------------------------------------------------- #
def _model_chat(messages, tools=None):
    """Call the local model. Returns an ollama message object, or None if the
    runtime is unreachable (caller then falls back to a static reply)."""
    try:
        import ollama
        resp = ollama.chat(
            model=MODEL,
            messages=messages,
            tools=tools or None,
            think=False,  # disable chatty reasoning: faster + more direct tool calls
            options={"temperature": 0.1},  # favor consistent tool selection
        )
        return resp.message
    except Exception:
        return None


def _clean(content):
    """Strip qwen3-style <think>...</think> reasoning some models emit inline, so
    only the spoken answer reaches the user (and TTS)."""
    text = re.sub(r"(?is)<think>.*?</think>", "", content or "")
    text = text.replace("<think>", "").replace("</think>", "")
    return text.strip()


def _as_function_tool(tool):
    """Convert a module TOOLS entry into an Ollama function-tool schema. The
    when_to_use hint is folded into the description so the model sees it."""
    desc = tool["description"]
    if tool.get("when_to_use"):
        desc = f"{desc} Use when: {tool['when_to_use']}"
    return {"type": "function",
            "function": {"name": tool["name"], "description": desc,
                         "parameters": tool["parameters"]}}


# --------------------------------------------------------------------------- #
# Grounding (RAG) — compact, capped snapshot of all four stores
# --------------------------------------------------------------------------- #
def _cap(items, n=10):
    return items[:n]


def _grounding():
    """Read-only summary of current suite state for the model's context."""
    lines = [f"Today is {date.today().isoformat()}.", ""]

    b = MODULES["budget"]
    summ = b.get_summary()
    lines.append("BUDGET:")
    if summ.get("ok"):
        lines.append(f"  balance ${summ['balance']:.2f}; this month "
                     f"(+${summ['month_earned']:.2f} / -${summ['month_spent']:.2f} "
                     f"= net ${summ['month_net']:.2f})")
    over = [l for l in b.limit_status().get("limits", []) if l.get("over")]
    if over:
        lines.append("  over limit: " + ", ".join(l["category"] for l in over))
    due = [bl for bl in b.bill_status().get("bills", []) if bl.get("due_soon")]
    if due:
        lines.append("  bills due soon: "
                     + ", ".join(f"{d['name']} ({d['next_due']})" for d in _cap(due)))

    ev = _cap(MODULES["calendar"].list_events().get("events", []))
    lines.append("CALENDAR (upcoming):")
    lines += [f"  [{i}] {e.get('date','?')} {e.get('time','')} {e['title']} "
              f"[{e.get('priority','')}]" for i, e in enumerate(ev)] or ["  (none)"]

    items = _cap(MODULES["checklist"].list_items("open").get("items", []))
    lines.append("CHECKLIST (open):")
    lines += [f"  [{i}] {it['item']} [{it.get('priority','')}]"
              for i, it in enumerate(items)] or ["  (none)"]

    hb = _cap(MODULES["habits"].list_habits().get("habits", []))
    lines.append("HABITS:")
    lines += [f"  {h['name']} ({h['count']} done, last {h.get('last') or 'never'})"
              for h in hb] or ["  (none)"]

    return "\n".join(lines)


_PERSONA = (
    "You are a concise business assistant managing the user's budget, calendar, "
    "checklist, and habits. Reply in one or two short, plain sentences suitable to "
    "be read aloud. Give only the final answer — never show your reasoning. Never "
    "invent data; rely on the summary and tool results."
)


# --------------------------------------------------------------------------- #
# Two-stage routing
# --------------------------------------------------------------------------- #
_SELECT_MODULE_TOOL = {
    "type": "function",
    "function": {
        "name": "select_module",
        "description": (
            "Route the user's request to a suite module. ALWAYS call this exactly "
            "once. Pick 'budget', 'calendar', 'checklist', or 'habits' for anything "
            "about money, scheduling, tasks/to-dos, or habits — especially any request "
            "to add, change, complete, or delete something. Pick 'none' ONLY for pure "
            "small talk or questions unrelated to those four areas."),
        "parameters": {
            "type": "object",
            "properties": {"module": {"type": "string",
                                      "enum": list(MODULES) + ["none"]}},
            "required": ["module"],
        },
    },
}

# Keyword safety-net: a small model sometimes free-texts a fake success instead of
# calling select_module. If the message clearly targets a module, force the route so
# the write actually runs. Order matters (habits before calendar so "did X today" wins).
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
             "sunday", "today", "tomorrow", "tonight")
_KEYWORDS = [
    ("budget", ("balance", "spend", "spent", "budget", "bill", "income", "salary",
                "expense", "transaction", "saving", "goal", "afford", "$", "dollar",
                "paid", "pay ", "earn", "money", "limit", "cost")),
    ("habits", ("habit", "streak", "workout", "every day", "everyday", "daily",
                "did my", "track ", "meditat", "exercise")),
    ("calendar", ("schedule", "appointment", "meeting", "event", "calendar",
                  "remind me", "book ", "reschedule", "free on", "free this")),
    ("checklist", ("checklist", "to-do", "todo", "to do", "task", " list", "mark ",
                   "cross off", "need to", "get done", "done")),
]


def _keyword_route(text):
    t = f" {text.lower()} "
    for module, words in _KEYWORDS:
        if any(w in t for w in words):
            return module
    if any(d in t for d in _WEEKDAYS):
        return "calendar"
    return None


def _stage1(user_message, grounding):
    """Pick a module, or answer directly from grounding. Returns
    (module_name | None, direct_reply | None)."""
    messages = [
        {"role": "system", "content": f"{_PERSONA}\n\nCurrent state:\n{grounding}"},
        {"role": "user", "content": user_message},
    ]
    msg = _model_chat(messages, tools=[_SELECT_MODULE_TOOL])
    if msg is None:
        return None, None  # runtime down -> caller handles fallback
    chosen = None
    for call in (msg.tool_calls or []):
        if call.function.name == "select_module":
            chosen = dict(call.function.arguments).get("module")
            break
    if chosen in MODULES:
        return chosen, None
    # Model chose 'none' or free-texted. Trust a keyword match over a possibly
    # hallucinated answer so mutations aren't silently dropped.
    net = _keyword_route(user_message)
    if net:
        return net, None
    return None, _clean(msg.content) or None


def _stage2(module_name, user_message, grounding, max_hops=4):
    """Offer only the chosen module's tools; run a bounded tool-use loop.
    Returns (reply, action, results, pending)."""
    module = MODULES[module_name]
    manifest = module.describe()
    tools = [_as_function_tool(t) for t in manifest["tools"]]
    messages = [
        {"role": "system",
         "content": (f"{_PERSONA}\n\nModule: {module_name}\n"
                     f"{manifest['usage_rules']}\n\n"
                     "To act, CALL the matching tool — including for delete/remove "
                     "requests. Do NOT write your own confirmation question; the "
                     "system adds confirmation automatically when a tool is "
                     f"destructive.\n\nCurrent state:\n{grounding}")},
        {"role": "user", "content": user_message},
    ]
    results = []
    for _ in range(max_hops):
        msg = _model_chat(messages, tools=tools)
        if msg is None:
            return _HELP, "fallback", results, None
        calls = msg.tool_calls or []
        if not calls:
            reply = _clean(msg.content) or "Done."
            return reply, ("tool_ok" if results else "answered"), results, None
        messages.append(msg)
        for call in calls:
            action = call.function.name
            params = dict(call.function.arguments or {})
            res = module.execute(action, params)
            results.append(res)
            if res.get("needs_confirmation"):
                # Pause the whole turn: store the pending action, ask the user with a
                # plain-language prompt (the module's raw message is dev-facing).
                pending = {"module": module_name, "action": action,
                           "params": params, "ts": datetime.now().isoformat()}
                reply = (f"Please confirm: {action.replace('_', ' ')}. "
                         "This can't be undone. Reply 'yes' to confirm or 'no' to "
                         "cancel.")
                return reply, "needs_confirmation", results, pending
            messages.append({"role": "tool", "tool_name": action,
                             "content": json.dumps(res)})
    return "Stopped after several steps — please rephrase.", "tool_ok", results, None


# --------------------------------------------------------------------------- #
# Confirmation resolution (deterministic, runs before routing)
# --------------------------------------------------------------------------- #
def _norm(text):
    return re.sub(r"[.!?]+$", "", str(text).strip().lower())


def _pending_alive(pending):
    if not pending:
        return False
    try:
        age = (datetime.now() - datetime.fromisoformat(pending["ts"])).total_seconds()
    except (KeyError, ValueError):
        return False
    return age <= _PENDING_TTL_SECONDS


def _resolve_pending(store, text):
    """If a live pending confirmation exists and the text is yes/no, resolve it.
    Returns an envelope dict, or None to let normal routing proceed."""
    pending = store.get("pending")
    if not _pending_alive(pending):
        store["pending"] = None
        return None
    word = _norm(text)
    if word in _AFFIRM:
        module = MODULES[pending["module"]]
        res = module.execute(pending["action"], pending["params"], confirm=True)
        store["pending"] = None
        reply = "Done." if res.get("ok") else f"Couldn't do that: {res.get('error')}"
        return {"reply": reply, "action": "confirmed", "results": [res]}
    if word in _DENY:
        store["pending"] = None
        return {"reply": "Okay, cancelled — nothing changed.",
                "action": "canceled", "results": []}
    # Neither yes nor no: drop the stale prompt and treat as ordinary input.
    store["pending"] = None
    return None


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def chat(message):
    """Process one user turn; returns {reply, action, results}."""
    text = str(message or "").strip()
    if not text:
        return {"reply": "What would you like to do? You can ask about your budget, "
                         "calendar, checklist, or habits.",
                "action": "empty", "results": []}

    store = _load_store()
    _record(store, "user", text)

    # 1) Resolve an outstanding confirmation first.
    resolved = _resolve_pending(store, text)
    if resolved is not None:
        _record(store, "assistant", resolved["reply"])
        _save_store(store)
        return resolved

    # 2) Route: stage 1 (module or direct answer) -> stage 2 (act).
    grounding = _grounding()
    module_name, direct = _stage1(text, grounding)

    if module_name is None and direct is None:
        # Model runtime unreachable.
        _record(store, "assistant", _HELP)
        _save_store(store)
        return {"reply": _HELP, "action": "fallback", "results": []}

    if module_name is None:
        _record(store, "assistant", direct)
        _save_store(store)
        return {"reply": direct, "action": "answered", "results": []}

    reply, action, results, pending = _stage2(module_name, text, grounding)
    store["pending"] = pending
    _record(store, "assistant", reply)
    _save_store(store)
    return {"reply": reply, "action": action, "results": results}


def history(limit=30):
    """Recent conversation, oldest-first, capped to `limit`."""
    store = _load_store()
    try:
        limit = max(1, int(limit))
    except (TypeError, ValueError):
        limit = 30
    return {"history": store["history"][-limit:]}


def clear_history():
    _save_store({"history": [], "pending": None})
    return {"ok": True}
