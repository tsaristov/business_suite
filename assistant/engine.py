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
    stage 1 -> pick the relevant module(s)  (can be several: "what's my day")
    stage 2 -> pick each module's action (<=17 choices), then synthesize one reply
- Grounding (RAG): a compact, capped snapshot of all four stores is read fresh each
  turn so the model can answer factual questions without holding the whole database.
- Confirmation is deterministic (a yes/no word list), never judged by the model.
- History lives in named, persistent sessions (sidebar in the UI).

Public API (used by app.py):
    chat(message, session_id=None)        -> {reply, action, results, modules, changed}
    chat_events(message, session_id=None) -> generator of {stage}/{final} events
    history(limit, session_id=None)       -> {session_id, history: [...]}
    clear_history(session_id=None)        -> {ok, session_id}
    list_sessions() / new_session() / switch_session(id) /
    rename_session(id, title) / delete_session(id)
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
# Store — multiple named chat sessions, each with its own history + pending slot.
# Shape: {"sessions": [{id, title, created_at, updated_at, history, pending}],
#         "active_id": <id>}
# --------------------------------------------------------------------------- #
def _now():
    return datetime.now().isoformat(timespec="seconds")


def _new_id():
    return datetime.now().strftime("%Y%m%d%H%M%S%f")


def _blank_session(title="New chat"):
    ts = _now()
    return {"id": _new_id(), "title": title, "created_at": ts, "updated_at": ts,
            "history": [], "pending": None}


def _load_store():
    # `dirty` = we synthesized/migrated something and must persist it, so ids stay
    # stable across pure reads (list_sessions/history) that don't otherwise save.
    dirty = False
    if not os.path.exists(_DATA) or os.path.getsize(_DATA) == 0:
        s = _blank_session()
        data = {"sessions": [s], "active_id": s["id"]}
        _save_store(data)
        return data
    with open(_DATA) as f:
        data = json.load(f)
    # Migrate the old single-conversation shape into one session.
    if "sessions" not in data:
        s = _blank_session()
        s["history"] = data.get("history", [])
        s["pending"] = data.get("pending")
        data = {"sessions": [s], "active_id": s["id"]}
        dirty = True
    if not data.get("sessions"):
        s = _blank_session()
        data["sessions"] = [s]
        data["active_id"] = s["id"]
        dirty = True
    for sess in data["sessions"]:
        sess.setdefault("history", [])
        sess.setdefault("pending", None)
    if data.get("active_id") not in {s["id"] for s in data["sessions"]}:
        data["active_id"] = data["sessions"][0]["id"]
        dirty = True
    if dirty:
        _save_store(data)
    return data


def _save_store(data):
    with open(_DATA, "w") as f:
        json.dump(data, f, indent=2)


def _get_session(store, session_id=None):
    """Return the requested session, or the active one. Falls back to the first."""
    sid = session_id or store.get("active_id")
    for sess in store["sessions"]:
        if sess["id"] == sid:
            return sess
    return store["sessions"][0]


def _record(session, role, message):
    session["history"].append({
        "role": role, "message": message, "created_at": _now(),
    })
    session["updated_at"] = _now()
    # Auto-title a fresh session from its first user message.
    if role == "user" and session.get("title") in (None, "", "New chat"):
        session["title"] = (message[:40] + "…") if len(message) > 40 else message


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
    now = datetime.now()
    lines = [f"Today is {now.strftime('%A %Y-%m-%d')}, current time "
             f"{now.strftime('%H:%M')}.", ""]

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
_SELECT_MODULES_TOOL = {
    "type": "function",
    "function": {
        "name": "select_modules",
        "description": (
            "Route the user's request to the relevant suite modules. ALWAYS call this "
            "exactly once. Include EVERY module the request touches — for a broad "
            "question like 'what does my day look like' pick calendar, checklist, AND "
            "habits. Pick a module for anything about money (budget), scheduling "
            "(calendar), tasks/to-dos (checklist), or habits — especially any request "
            "to add, change, complete, or delete something. Use an empty list ONLY for "
            "pure small talk unrelated to those four areas."),
        "parameters": {
            "type": "object",
            "properties": {
                "modules": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(MODULES)},
                    "description": "All relevant modules, in the order to handle them.",
                }
            },
            "required": ["modules"],
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


# Broad "how's my day" queries should always span calendar + checklist + habits — small
# models tend to pick just one module, so force the set deterministically.
_OVERVIEW = ("my day", "the day", "look like today", "today look", "my agenda",
             "agenda", "overview", "rundown", "schedule today", "have today",
             "on today", "brief me", "catch me up", "what do i have")
_OVERVIEW_MODULES = ["calendar", "checklist", "habits"]


def _is_overview(text):
    t = text.lower()
    return any(p in t for p in _OVERVIEW)


def _stage1(user_message, grounding):
    """Pick the relevant module(s), or answer directly from grounding. Returns
    (modules: list, direct_reply | None). An empty list + None reply means the model
    runtime was unreachable."""
    messages = [
        {"role": "system", "content": f"{_PERSONA}\n\nCurrent state:\n{grounding}"},
        {"role": "user", "content": user_message},
    ]
    msg = _model_chat(messages, tools=[_SELECT_MODULES_TOOL])
    if msg is None:
        return [], None  # runtime down -> caller handles fallback
    chosen = []
    for call in (msg.tool_calls or []):
        if call.function.name == "select_modules":
            raw = dict(call.function.arguments).get("modules") or []
            if isinstance(raw, str):
                raw = [raw]
            chosen = [m for m in raw if m in MODULES]
            break
    # Force the full set for broad day-overview questions (models under-select here).
    overview = _OVERVIEW_MODULES if _is_overview(user_message) else []
    if chosen or overview:
        # De-dupe, preserve order.
        return list(dict.fromkeys(chosen + overview)), None
    # Model returned nothing / free-texted. Trust a keyword match over a possibly
    # hallucinated answer so mutations aren't silently dropped.
    net = _keyword_route(user_message)
    if net:
        return [net], None
    return [], _clean(msg.content) or None


# Read-only tool names across all modules; anything else that succeeds is a write
# (drives the `changed` flag for auto-refreshing the affected tab).
_READ_ACTIONS = {
    "get_summary", "list_transactions", "list_categories", "limit_status",
    "bill_status", "goal_status", "spending_breakdown",
    "list_events", "list_items", "list_habits",
}


def _stage2_events(module_name, user_message, grounding, max_hops=4):
    """Offer only the chosen module's tools; run a bounded tool-use loop.

    A generator: yields ("stage", label) progress events as it works, then a single
    terminal ("done", reply, action, results, pending, wrote) tuple, where `wrote` is
    True if a successful mutating tool ran (so the caller can refresh that tab).
    """
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
    wrote = False
    for _ in range(max_hops):
        yield ("stage", "Deciding next step")
        msg = _model_chat(messages, tools=tools)
        if msg is None:
            yield ("done", _HELP, "fallback", results, None, wrote)
            return
        calls = msg.tool_calls or []
        if not calls:
            yield ("stage", "Generating reply")
            reply = _clean(msg.content) or "Done."
            yield ("done", reply, ("tool_ok" if results else "answered"),
                   results, None, wrote)
            return
        messages.append(msg)
        for call in calls:
            action = call.function.name
            params = dict(call.function.arguments or {})
            yield ("stage", f"Using {module_name}: {action.replace('_', ' ')}")
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
                yield ("done", reply, "needs_confirmation", results, pending, wrote)
                return
            if res.get("ok") and action not in _READ_ACTIONS:
                wrote = True
            messages.append({"role": "tool", "tool_name": action,
                             "content": json.dumps(res)})
    yield ("done", "Stopped after several steps — please rephrase.",
           "tool_ok", results, None, wrote)


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


def _resolve_pending(session, text):
    """If a live pending confirmation exists and the text is yes/no, resolve it.
    Returns an envelope dict (with `modules`/`changed`), or None to let normal
    routing proceed."""
    pending = session.get("pending")
    if not _pending_alive(pending):
        session["pending"] = None
        return None
    word = _norm(text)
    if word in _AFFIRM:
        module_name = pending["module"]
        res = MODULES[module_name].execute(pending["action"], pending["params"],
                                           confirm=True)
        session["pending"] = None
        reply = "Done." if res.get("ok") else f"Couldn't do that: {res.get('error')}"
        return {"reply": reply, "action": "confirmed", "results": [res],
                "modules": [module_name], "changed": bool(res.get("ok"))}
    if word in _DENY:
        session["pending"] = None
        return {"reply": "Okay, cancelled — nothing changed.",
                "action": "canceled", "results": [], "modules": [], "changed": False}
    # Neither yes nor no: drop the stale prompt and treat as ordinary input.
    session["pending"] = None
    return None


# --------------------------------------------------------------------------- #
# Synthesis — combine several modules' tool results into one reply
# --------------------------------------------------------------------------- #
def _synthesize(user_message, grounding, collected):
    """One model call that turns per-module tool results into a single short reply.
    `collected` is a list of (module_name, results). Falls back to a plain join."""
    blob = "\n".join(f"{m}: {json.dumps(r)}" for m, r in collected)
    messages = [
        {"role": "system",
         "content": (f"{_PERSONA}\n\nCurrent state:\n{grounding}\n\n"
                     "Combine the tool results below into ONE short, plain answer to "
                     "the user. Mention each area briefly; skip empty ones.\n\n"
                     f"Tool results:\n{blob}")},
        {"role": "user", "content": user_message},
    ]
    msg = _model_chat(messages)
    reply = _clean(msg.content) if msg else None
    return reply or "Here's what I found across your tools."


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def chat_events(message, session_id=None):
    """Process one user turn as a stream of events on a chat session.

    Yields progress events `{"stage": "<label>"}` as the pipeline works, then exactly
    one terminal event `{"final": {reply, action, results, modules, changed}}`. The
    chat() wrapper drains this for non-streaming callers; the streaming HTTP route
    relays every event so the UI can show a live timeline.
    """
    def final(reply, action, results, modules=None, changed=False):
        return {"final": {"reply": reply, "action": action, "results": results,
                          "modules": modules or [], "changed": changed}}

    text = str(message or "").strip()
    if not text:
        yield final("What would you like to do? You can ask about your budget, "
                    "calendar, checklist, or habits.", "empty", [])
        return

    store = _load_store()
    session = _get_session(store, session_id)
    _record(session, "user", text)

    # 1) Resolve an outstanding confirmation first.
    resolved = _resolve_pending(session, text)
    if resolved is not None:
        yield {"stage": "Confirming action"}
        _record(session, "assistant", resolved["reply"])
        _save_store(store)
        yield final(resolved["reply"], resolved["action"], resolved["results"],
                    resolved["modules"], resolved["changed"])
        return

    # 2) Route: stage 1 (which modules?) -> stage 2 per module -> synthesize.
    yield {"stage": "Reading your data"}
    grounding = _grounding()

    yield {"stage": "Detecting tool usage"}
    modules, direct = _stage1(text, grounding)

    if not modules and direct is None:
        # Model runtime unreachable.
        _record(session, "assistant", _HELP)
        _save_store(store)
        yield final(_HELP, "fallback", [])
        return

    if not modules:
        yield {"stage": "Generating reply"}
        _record(session, "assistant", direct)
        _save_store(store)
        yield final(direct, "answered", [])
        return

    all_results = []
    collected = []          # (module, results) for synthesis
    per_module_reply = None
    changed = False
    acted = []
    for module_name in modules:
        yield {"stage": f"Checking {module_name}"}
        m_reply = m_results = m_pending = None
        m_wrote = False
        for ev in _stage2_events(module_name, text, grounding):
            if ev[0] == "stage":
                yield {"stage": ev[1]}
            else:
                _, m_reply, m_action, m_results, m_pending, m_wrote = ev
        acted.append(module_name)
        all_results.extend(m_results or [])
        collected.append((module_name, m_results or []))
        per_module_reply = m_reply
        changed = changed or m_wrote
        # A destructive step pauses the whole turn immediately.
        if m_pending is not None:
            session["pending"] = m_pending
            _record(session, "assistant", m_reply)
            _save_store(store)
            yield final(m_reply, "needs_confirmation", all_results, acted, changed)
            return

    session["pending"] = None
    if len(acted) > 1:
        yield {"stage": "Composing summary"}
        reply = _synthesize(text, grounding, collected)
    else:
        reply = per_module_reply or "Done."
    _record(session, "assistant", reply)
    _save_store(store)
    yield final(reply, "tool_ok", all_results, acted, changed)


def chat(message, session_id=None):
    """Process one user turn; returns the final envelope. Thin wrapper over
    chat_events() for non-streaming callers (tests, the plain JSON route)."""
    result = {"reply": "", "action": "error", "results": [], "modules": [],
              "changed": False}
    for ev in chat_events(message, session_id):
        if "final" in ev:
            result = ev["final"]
    return result


def history(limit=30, session_id=None):
    """Recent conversation for a session, oldest-first, capped to `limit`."""
    store = _load_store()
    session = _get_session(store, session_id)
    try:
        limit = max(1, int(limit))
    except (TypeError, ValueError):
        limit = 30
    return {"session_id": session["id"], "history": session["history"][-limit:]}


def clear_history(session_id=None):
    """Wipe a single session's history + pending (keeps the session itself)."""
    store = _load_store()
    session = _get_session(store, session_id)
    session["history"] = []
    session["pending"] = None
    session["title"] = "New chat"
    _save_store(store)
    return {"ok": True, "session_id": session["id"]}


# --------------------------------------------------------------------------- #
# Session management
# --------------------------------------------------------------------------- #
def _session_summary(sess):
    return {"id": sess["id"], "title": sess.get("title") or "New chat",
            "created_at": sess.get("created_at"), "updated_at": sess.get("updated_at"),
            "count": len(sess.get("history", []))}


def list_sessions():
    """All sessions (newest-updated first) + the active id."""
    store = _load_store()
    sessions = sorted(store["sessions"], key=lambda s: s.get("updated_at", ""),
                      reverse=True)
    return {"active_id": store["active_id"],
            "sessions": [_session_summary(s) for s in sessions]}


def new_session():
    """Create a fresh session and make it active."""
    store = _load_store()
    sess = _blank_session()
    store["sessions"].append(sess)
    store["active_id"] = sess["id"]
    _save_store(store)
    return {"ok": True, "session": _session_summary(sess)}


def switch_session(session_id):
    store = _load_store()
    if session_id not in {s["id"] for s in store["sessions"]}:
        return {"ok": False, "error": "no such session"}
    store["active_id"] = session_id
    _save_store(store)
    return {"ok": True, "active_id": session_id}


def rename_session(session_id, title):
    store = _load_store()
    title = str(title or "").strip()
    if not title:
        return {"ok": False, "error": "title is required"}
    for sess in store["sessions"]:
        if sess["id"] == session_id:
            sess["title"] = title[:60]
            _save_store(store)
            return {"ok": True, "session": _session_summary(sess)}
    return {"ok": False, "error": "no such session"}


def delete_session(session_id):
    store = _load_store()
    before = len(store["sessions"])
    store["sessions"] = [s for s in store["sessions"] if s["id"] != session_id]
    if len(store["sessions"]) == before:
        return {"ok": False, "error": "no such session"}
    if not store["sessions"]:                      # never leave zero sessions
        store["sessions"] = [_blank_session()]
    if store["active_id"] == session_id:
        store["active_id"] = store["sessions"][0]["id"]
    _save_store(store)
    return {"ok": True, "active_id": store["active_id"]}
