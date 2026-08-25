"""Assistant engine — a generic function-calling runtime.

The engine knows how to run tools, not what they mean. It:
  - discovers tools dynamically (every tools/<name>/agent.py),
  - presents them to the LLM as one flat, namespaced tool set (module__action),
  - executes the calls the LLM makes and feeds results back,
  - handles destructive-action confirmations,
  - maintains conversation sessions.

All domain meaning lives in the tools: what each tool is for (its description /
when_to_use), a read-only `context()` a tool may expose, and destructive-action
validation + previews (the tool decides). There are NO hardcoded keywords, module
names, routing rules, or date logic here. The LLM does the semantic understanding.

The model runtime is a LOCAL Ollama model behind one adapter (`_model_chat`); any
provider could replace it.

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
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_HERE)
_TOOLS_DIR = os.path.join(_BASE, "tools")
_DATA = os.path.join(_HERE, "data.json")

# A capable, non-reasoning tool-caller. All tools are shown in one call, so the model
# must handle many functions + genuine intent understanding. Override with OLLAMA_MODEL.
# This is only the fallback; the live model/temperature/prompt come from settings.py so
# the user can tweak them from the Settings tab without a restart.
MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:latest")


def _load_sibling(filename, modname):
    """Load a module living next to engine.py by path (engine runs outside a package)."""
    path = os.path.join(_HERE, filename)
    spec = importlib.util.spec_from_file_location(modname, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


settings = _load_sibling("settings.py", "assistant_settings")

# Uploaded document text is truncated to keep prompts sane.
_MAX_ATTACH_CHARS = 12000

_MAX_HOPS = 6          # bound the tool-use loop per turn
_PENDING_TTL_SECONDS = 5 * 60

# The ONE hardcoded word set — for resolving a yes/no confirmation, not routing.
_AFFIRM = {"yes", "y", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm",
           "confirmed", "do it", "go ahead", "please do", "affirmative"}
_DENY = {"no", "n", "nope", "nah", "cancel", "never mind", "nevermind", "stop",
         "don't", "dont", "negative"}

_HELP = "I can't reach the assistant model right now — make sure Ollama is running."

_PERSONA = (
    "You are a helpful assistant with access to the user's tools. Decide which tools to "
    "call to satisfy the request; you may call several, across different tools, in one "
    "turn. To act on a specific existing item (update, complete, delete), FIRST call the "
    "matching list/status tool and use the exact identifier (e.g. index) it returns — "
    "never guess an index. If a tool was used, include ALL data from its output that is "
    "relevant to the user's question — do not omit items. Answer concisely in plain "
    "language suitable to be read aloud, and give only the final answer, not your reasoning."
)


# --------------------------------------------------------------------------- #
# Tool discovery & registry (the engine's only knowledge of tools)
# --------------------------------------------------------------------------- #
def _load(name, *parts):
    path = os.path.join(_TOOLS_DIR, *parts)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _discover_modules():
    """Every tools/<name>/agent.py becomes an available module. No hardcoded list."""
    mods = {}
    if not os.path.isdir(_TOOLS_DIR):
        return mods
    for name in sorted(os.listdir(_TOOLS_DIR)):
        agent = os.path.join(_TOOLS_DIR, name, "agent.py")
        if os.path.isfile(agent):
            try:
                mods[name] = _load(f"assistant_{name}_agent", name, "agent.py")
            except Exception:
                pass  # a broken tool must not take down the whole assistant
    return mods


MODULES = _discover_modules()


def _build_registry():
    """Flatten every module's tools into one namespaced function-tool list.
    Returns (ollama_specs, dispatch{fullname:(module,action)}, mutating{fullname})."""
    specs, dispatch, mutating = [], {}, set()
    for mod_name, mod in MODULES.items():
        try:
            tools = mod.describe().get("tools", [])
        except Exception:
            tools = []
        for t in tools:
            full = f"{mod_name}__{t['name']}"
            dispatch[full] = (mod_name, t["name"])
            if t.get("mutates"):
                mutating.add(full)
            desc = t.get("description", "")
            if t.get("when_to_use"):
                desc = f"{desc} Use when: {t['when_to_use']}"
            specs.append({"type": "function",
                          "function": {"name": full, "description": desc,
                                       "parameters": t.get("parameters", {})}})
    return specs, dispatch, mutating


_TOOLSPECS, _DISPATCH, _MUTATING = _build_registry()


def _context_block():
    """Concatenate each tool's optional read-only context(). The engine does not parse
    or understand the contents — tools decide what (if anything) to surface."""
    parts = []
    for name, mod in MODULES.items():
        fn = getattr(mod, "context", None)
        if callable(fn):
            try:
                text = fn()
            except Exception:
                text = None
            if text:
                parts.append(f"## {name}\n{text}")
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# Store — multiple named chat sessions, each with history + a pending slot.
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
    dirty = False
    if not os.path.exists(_DATA) or os.path.getsize(_DATA) == 0:
        s = _blank_session()
        data = {"sessions": [s], "active_id": s["id"]}
        _save_store(data)
        return data
    with open(_DATA) as f:
        data = json.load(f)
    if "sessions" not in data:                      # migrate old single-conversation
        s = _blank_session()
        s["history"] = data.get("history", [])
        s["pending"] = data.get("pending")
        data = {"sessions": [s], "active_id": s["id"]}
        dirty = True
    if not data.get("sessions"):
        s = _blank_session()
        data["sessions"], data["active_id"] = [s], s["id"]
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
    sid = session_id or store.get("active_id")
    for sess in store["sessions"]:
        if sess["id"] == sid:
            return sess
    return store["sessions"][0]


def _record(session, role, message):
    session["history"].append({"role": role, "message": message, "created_at": _now()})
    session["updated_at"] = _now()
    if role == "user" and session.get("title") in (None, "", "New chat"):
        session["title"] = (message[:40] + "…") if len(message) > 40 else message


def _recent_history(session, limit=10):
    """Recent turns as model messages (already includes the current user turn)."""
    out = []
    for h in session["history"][-limit:]:
        role = "assistant" if h["role"] == "assistant" else "user"
        out.append({"role": role, "content": h["message"]})
    return out


# --------------------------------------------------------------------------- #
# Ollama adapter (the only provider-specific code)
# --------------------------------------------------------------------------- #
def _model_chat(messages, tools=None, cfg=None):
    """Call the local model. Returns the message object, or None if unreachable.
    Model tag and temperature come from settings (cfg), falling back to defaults."""
    cfg = cfg or {}
    try:
        import ollama
        resp = ollama.chat(
            model=cfg.get("model") or MODEL,
            messages=messages,
            tools=tools or None,
            think=False,
            options={"temperature": cfg.get("temperature", 0.1)},
        )
        return resp.message
    except Exception:
        return None


def _vision_describe(image_b64, cfg):
    """Describe an uploaded image with the vision model. Returns text or None.
    Keeps vision separate from the tool-calling loop so the main model stays text-only."""
    try:
        import ollama
        resp = ollama.chat(
            model=cfg.get("vision_model") or settings.DEFAULTS["vision_model"],
            messages=[{"role": "user",
                       "content": "Describe this image in detail, including any visible "
                                  "text, numbers, charts, or documents.",
                       "images": [image_b64]}],
            options={"temperature": 0.1},
        )
        return _clean(resp.message.content)
    except Exception:
        return None


def _extract_file_text(name, mime, raw):
    """Extract text from an uploaded document (pdf/docx/plain). Returns '' on failure.
    Heavy parsers are imported lazily so a missing dep only disables that file type."""
    lower = str(name or "").lower()
    mime = (mime or "").lower()
    text = ""
    try:
        if lower.endswith(".pdf") or mime == "application/pdf":
            import io
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(raw))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        elif lower.endswith(".docx") or "word" in mime or "officedocument" in mime:
            import tempfile
            import docx2txt
            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tf:
                tf.write(raw)
                path = tf.name
            try:
                text = docx2txt.process(path) or ""
            finally:
                os.remove(path)
        else:
            text = raw.decode("utf-8", errors="ignore")
    except Exception:
        return ""
    text = text.strip()
    if len(text) > _MAX_ATTACH_CHARS:
        text = text[:_MAX_ATTACH_CHARS] + "…"
    return text


def _ingest_attachments(attachments, cfg):
    """Turn uploaded files into text to append to the user's message. Images are
    described by the vision model; documents are text-extracted. Returns '' if nothing
    usable. `attachments` items: {name, mime, data_b64} (data_b64 may be a data URL)."""
    if not attachments:
        return ""
    import base64
    parts = []
    for att in attachments:
        name = att.get("name", "file")
        mime = (att.get("mime") or "").lower()
        b64 = att.get("data_b64") or ""
        if b64.startswith("data:") and "," in b64:
            b64 = b64.split(",", 1)[1]  # strip the data URL prefix
        try:
            raw = base64.b64decode(b64)
        except Exception:
            continue
        if mime.startswith("image/"):
            desc = _vision_describe(b64, cfg) or "(could not read this image)"
            parts.append(f"[Image: {name}]\n{desc}")
        else:
            body = _extract_file_text(name, mime, raw)
            parts.append(f"[File: {name}]\n{body or '(could not read this file)'}")
    if not parts:
        return ""
    return "\n\nAttached files (provided by the user):\n" + "\n\n".join(parts)


def _clean(content):
    """Strip any <think>...</think> reasoning a model might emit inline."""
    text = re.sub(r"(?is)<think>.*?</think>", "", content or "")
    return text.replace("<think>", "").replace("</think>", "").strip()


# --------------------------------------------------------------------------- #
# Confirmation resolution (deterministic yes/no; runs before the model)
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
    """Resolve a live yes/no confirmation. Returns an envelope, or None to proceed."""
    pending = session.get("pending")
    if not _pending_alive(pending):
        session["pending"] = None
        return None
    word = _norm(text)
    if word in _AFFIRM:
        mod_name, action = pending["module"], pending["action"]
        res = MODULES[mod_name].execute(action, pending["params"], confirm=True)
        session["pending"] = None
        reply = "Done." if res.get("ok") else f"Couldn't do that: {res.get('error')}"
        return {"reply": reply, "action": "confirmed", "results": [res],
                "modules": [mod_name] if res.get("ok") else [],
                "changed": bool(res.get("ok"))}
    if word in _DENY:
        session["pending"] = None
        return {"reply": "Okay, cancelled — nothing changed.", "action": "canceled",
                "results": [], "modules": [], "changed": False}
    session["pending"] = None      # neither yes nor no -> drop stale prompt, proceed
    return None


# --------------------------------------------------------------------------- #
# Public API — the generic function-calling loop
# --------------------------------------------------------------------------- #
def chat_events(message, session_id=None, attachments=None):
    """Process one turn as a stream of events: zero or more {"stage": ...}, then one
    {"final": {reply, action, results, modules, changed}}. `attachments` is an optional
    list of {name, mime, data_b64} uploaded alongside the text."""
    def final(reply, action, results, modules=None, changed=False):
        return {"final": {"reply": reply, "action": action, "results": results,
                          "modules": sorted(modules or []), "changed": changed}}

    text = str(message or "").strip()
    if not text and not attachments:
        yield final("What would you like to do?", "empty", [])
        return

    store = _load_store()
    session = _get_session(store, session_id)
    display = text or f"[sent {len(attachments)} attachment(s)]"
    _record(session, "user", display)

    # 1) Resolve an outstanding confirmation first (deterministic, no model call).
    resolved = _resolve_pending(session, text)
    if resolved is not None:
        yield {"stage": "Confirming action"}
        _record(session, "assistant", resolved["reply"])
        _save_store(store)
        yield final(resolved["reply"], resolved["action"], resolved["results"],
                    resolved["modules"], resolved["changed"])
        return

    cfg = settings.get()

    # 2) Present all tools + context; let the model call whatever it needs.
    yield {"stage": "Reading context"}
    ctx = _context_block()
    system = cfg.get("system_prompt") or _PERSONA
    rules = (cfg.get("rules") or "").strip()
    if rules:
        system += f"\n\nAdditional rules:\n{rules}"
    if ctx:
        system += f"\n\nCurrent context (read-only):\n{ctx}"
    messages = ([{"role": "system", "content": system}]
                + _recent_history(session, cfg.get("history_limit", 10)))

    # 2b) Fold any uploaded files into the current user turn as text.
    if attachments:
        yield {"stage": "Reading attachments"}
        attach_text = _ingest_attachments(attachments, cfg)
        if attach_text and messages and messages[-1].get("role") == "user":
            messages[-1]["content"] = (messages[-1].get("content") or "") + attach_text

    results, acted, changed = [], set(), False
    for _ in range(_MAX_HOPS):
        yield {"stage": "Thinking"}
        msg = _model_chat(messages, tools=_TOOLSPECS, cfg=cfg)
        if msg is None:
            _record(session, "assistant", _HELP)
            _save_store(store)
            yield final(_HELP, "fallback", results, acted, changed)
            return
        calls = msg.tool_calls or []
        if not calls:
            reply = _clean(msg.content) or "Done."
            session["pending"] = None
            _record(session, "assistant", reply)
            _save_store(store)
            yield final(reply, "tool_ok" if results else "answered",
                        results, acted, changed)
            return
        messages.append(msg)
        for call in calls:
            full = call.function.name
            params = dict(call.function.arguments or {})
            if full not in _DISPATCH:
                res = {"ok": False, "error": f"unknown tool '{full}'"}
                results.append(res)
                messages.append({"role": "tool", "tool_name": full,
                                 "content": json.dumps(res)})
                continue
            mod_name, action = _DISPATCH[full]
            yield {"stage": f"Calling {full}"}
            res = MODULES[mod_name].execute(action, params)
            results.append(res)
            if res.get("needs_confirmation"):
                session["pending"] = {"module": mod_name, "action": action,
                                      "params": params, "ts": datetime.now().isoformat()}
                reply = (res.get("message", "This action needs confirmation.")
                         + " Reply 'yes' to confirm or 'no' to cancel.")
                _record(session, "assistant", reply)
                _save_store(store)
                yield final(reply, "needs_confirmation", results, acted, changed)
                return
            if res.get("ok") and full in _MUTATING:
                changed = True
                acted.add(mod_name)
            messages.append({"role": "tool", "tool_name": full,
                             "content": json.dumps(res)})

    # Hop limit hit — ask once more, no tools, for a final answer from the results.
    yield {"stage": "Composing reply"}
    messages.append({"role": "system",
                     "content": "Answer the user now using the tool results above; "
                                "do not call any more tools."})
    msg = _model_chat(messages, cfg=cfg)
    reply = (_clean(msg.content) if msg else "") or "Done."
    session["pending"] = None
    _record(session, "assistant", reply)
    _save_store(store)
    yield final(reply, "tool_ok", results, acted, changed)


def chat(message, session_id=None, attachments=None):
    """Non-streaming wrapper — returns the final envelope."""
    result = {"reply": "", "action": "error", "results": [], "modules": [],
              "changed": False}
    for ev in chat_events(message, session_id, attachments):
        if "final" in ev:
            result = ev["final"]
    return result


def history(limit=30, session_id=None):
    store = _load_store()
    session = _get_session(store, session_id)
    try:
        limit = max(1, int(limit))
    except (TypeError, ValueError):
        limit = 30
    return {"session_id": session["id"], "history": session["history"][-limit:]}


def clear_history(session_id=None):
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
    store = _load_store()
    sessions = sorted(store["sessions"], key=lambda s: s.get("updated_at", ""),
                      reverse=True)
    return {"active_id": store["active_id"],
            "sessions": [_session_summary(s) for s in sessions]}


def new_session():
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
    if not store["sessions"]:
        store["sessions"] = [_blank_session()]
    if store["active_id"] == session_id:
        store["active_id"] = store["sessions"][0]["id"]
    _save_store(store)
    return {"ok": True, "active_id": store["active_id"]}
