"""Assistant settings — user-tweakable LLM configuration and instructions.

A tiny JSON-backed settings store, read at call time by the engine so changes take
effect without a restart. Holds the model choices, generation knobs, and the two pieces
of steering text the user can edit: the base system prompt and any extra rules.

Persisted to assistant/settings.json (git-ignored). Missing keys fall back to DEFAULTS,
so partial files and forward-compatible additions are safe.

Public API:
    get()            -> dict (DEFAULTS merged with the saved file)
    save(patch)      -> dict (persist a partial update, returns the merged result)
    reset()          -> dict (restore DEFAULTS)
"""

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_FILE = os.path.join(_HERE, "settings.json")

# The default persona matches the engine's original hardcoded behavior, so an untouched
# install behaves exactly as before. Users edit this (and `rules`) from the Settings tab.
_DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant with access to the user's tools. Decide which tools to "
    "call to satisfy the request; you may call several, across different tools, in one "
    "turn. To act on a specific existing item (update, complete, delete), FIRST call the "
    "matching list/status tool and use the exact identifier (e.g. index) it returns — "
    "never guess an index. If a tool was used, include ALL data from its output that is "
    "relevant to the user's question — do not omit items. Answer concisely in plain "
    "language suitable to be read aloud, and give only the final answer, not your reasoning."
)

DEFAULTS = {
    # Model used for the tool-calling chat loop (Ollama tag).
    "model": os.getenv("OLLAMA_MODEL", "llama3.1:latest"),
    # Vision model used to describe uploaded images before the text loop.
    "vision_model": os.getenv("OLLAMA_VISION_MODEL", "llava:latest"),
    # Embedding model used by the knowledge (RAG) tool.
    "embed_model": os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
    # Generation temperature (0.0 = factual, 1.0 = creative).
    "temperature": 0.1,
    # Chunks retrieved per knowledge search.
    "top_k": 3,
    # How many prior turns to feed the model as context.
    "history_limit": 10,
    # Editable steering text.
    "system_prompt": _DEFAULT_SYSTEM_PROMPT,
    "rules": "",
}

# Numeric fields get coerced so form posts (strings) don't poison the model call.
_NUMERIC = {"temperature": float, "top_k": int, "history_limit": int}


def _coerce(patch):
    out = dict(patch or {})
    for key, cast in _NUMERIC.items():
        if key in out and out[key] not in (None, ""):
            try:
                out[key] = cast(out[key])
            except (TypeError, ValueError):
                out.pop(key)
    return out


def _read_file():
    if not os.path.exists(_FILE) or os.path.getsize(_FILE) == 0:
        return {}
    try:
        with open(_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def get():
    """DEFAULTS overlaid with any saved values (only known keys are surfaced)."""
    saved = _read_file()
    cfg = dict(DEFAULTS)
    for key in DEFAULTS:
        if key in saved and saved[key] not in (None, ""):
            cfg[key] = saved[key]
    return _coerce(cfg)


def save(patch):
    """Persist a partial update; unknown keys are ignored. Returns the merged config."""
    current = _read_file()
    for key, value in _coerce(patch).items():
        if key in DEFAULTS:
            current[key] = value
    with open(_FILE, "w") as f:
        json.dump(current, f, indent=2)
    return get()


def reset():
    """Restore defaults by removing the saved file."""
    if os.path.exists(_FILE):
        os.remove(_FILE)
    return get()
