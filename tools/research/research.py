"""Research — basic web research producing a downloadable PDF report + short summary.

Flow: DuckDuckGo (DDGS, no API key) search -> fetch the top pages -> extract readable
text -> the local Ollama model synthesizes a structured report and a short summary ->
render a PDF with ReportLab. Report metadata is kept in data.json; PDFs live in reports/.

All network/heavy imports (ddgs, requests, bs4, reportlab, ollama) are lazy so the
module is cheap to import at tool discovery and a missing dep only breaks research.
"""

import importlib.util
import json
import os
import re
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPORTS_DIR = os.path.join(_HERE, "reports")
_DATA = os.path.join(_HERE, "data.json")

_PER_SOURCE_CHARS = 3000     # cap of extracted text kept per fetched page
_FETCH_TIMEOUT = 15
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def _now():
    return datetime.now().isoformat(timespec="seconds")


def load():
    if not os.path.exists(_DATA) or os.path.getsize(_DATA) == 0:
        return []
    try:
        with open(_DATA) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save(reports):
    with open(_DATA, "w") as f:
        json.dump(reports, f, indent=2)


def report_path(report_id):
    """Absolute PDF path for a report id, or None if the id looks unsafe/unknown."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "", str(report_id or ""))
    if not safe:
        return None
    return os.path.join(_REPORTS_DIR, f"{safe}.pdf")


def _load_settings():
    path = os.path.abspath(os.path.join(_HERE, "..", "..", "assistant", "settings.py"))
    try:
        spec = importlib.util.spec_from_file_location("assistant_settings_research", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.get()
    except Exception:
        return {"model": "llama3.1:latest", "temperature": 0.3}


# --------------------------------------------------------------------------- #
# Web search + fetch
# --------------------------------------------------------------------------- #
def _search(topic, max_sources):
    from ddgs import DDGS
    hits = DDGS().text(topic, max_results=max_sources) or []
    sources = []
    for h in hits:
        sources.append({
            "title": h.get("title", "") or "(untitled)",
            "url": h.get("href") or h.get("url") or "",
            "snippet": h.get("body", "") or "",
        })
    return [s for s in sources if s["url"]]


def _fetch_text(url):
    import requests
    from bs4 import BeautifulSoup
    try:
        resp = requests.get(url, timeout=_FETCH_TIMEOUT, headers={"User-Agent": _UA})
        resp.raise_for_status()
    except Exception:
        return ""
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n"))
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    return text[:_PER_SOURCE_CHARS]


# --------------------------------------------------------------------------- #
# LLM synthesis
# --------------------------------------------------------------------------- #
def _model_call(prompt, cfg, system=None):
    import ollama
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = ollama.chat(model=cfg.get("model") or "llama3.1:latest",
                       messages=messages, think=False,
                       options={"temperature": cfg.get("temperature", 0.3)})
    return (resp.message.content or "").strip()


def _synthesize(topic, sources, cfg):
    corpus = "\n\n".join(
        f"SOURCE {i + 1}: {s['title']} ({s['url']})\n{s.get('text') or s['snippet']}"
        for i, s in enumerate(sources))
    system = ("You are a research analyst. Write a clear, well-structured report grounded "
              "ONLY in the provided sources. Use section headings prefixed with '## ' and "
              "bullet points with '- '. Do not invent facts or citations.")
    report_prompt = (
        f"Topic: {topic}\n\nWrite a structured research report on this topic using the "
        f"sources below. Include an Overview, Key Findings, and a short Conclusion.\n\n"
        f"{corpus}")
    report = _model_call(report_prompt, cfg, system)
    summary_prompt = (f"In 2-3 sentences, summarize the key takeaway of this report on "
                      f"'{topic}':\n\n{report}")
    summary = _model_call(summary_prompt, cfg)
    return report, summary


# --------------------------------------------------------------------------- #
# PDF rendering
# --------------------------------------------------------------------------- #
def _esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _render_pdf(path, topic, summary, report, sources):
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (ListFlowable, ListItem, Paragraph, SimpleDocTemplate,
                                    Spacer)

    styles = getSampleStyleSheet()
    story = [
        Paragraph(_esc(topic), styles["Title"]),
        Paragraph(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                  styles["Italic"]),
        Spacer(1, 12),
        Paragraph("Summary", styles["Heading1"]),
        Paragraph(_esc(summary) or "—", styles["BodyText"]),
        Spacer(1, 12),
    ]
    for line in report.splitlines():
        s = line.strip()
        if not s:
            story.append(Spacer(1, 6))
        elif s.startswith("## "):
            story.append(Paragraph(_esc(s[3:]), styles["Heading2"]))
        elif s.startswith("# "):
            story.append(Paragraph(_esc(s[2:]), styles["Heading1"]))
        elif s.startswith(("- ", "* ")):
            story.append(Paragraph("• " + _esc(s[2:]), styles["BodyText"]))
        else:
            story.append(Paragraph(_esc(s), styles["BodyText"]))

    story += [Spacer(1, 12), Paragraph("Sources", styles["Heading1"])]
    items = [ListItem(Paragraph(f'{_esc(s["title"])}<br/>'
                                f'<font size=8 color="blue">{_esc(s["url"])}</font>',
                                styles["BodyText"]))
             for s in sources]
    if items:
        story.append(ListFlowable(items, bulletType="1"))

    os.makedirs(_REPORTS_DIR, exist_ok=True)
    SimpleDocTemplate(path, pagesize=letter).build(story)


# --------------------------------------------------------------------------- #
# Public operations
# --------------------------------------------------------------------------- #
def run_research(topic, max_sources=5):
    """Research `topic` and produce a PDF report + short summary.
    Returns {ok, report_id, title, summary, sources} or {ok: False, error}."""
    topic = str(topic or "").strip()
    if not topic:
        return {"ok": False, "error": "topic is required"}
    try:
        max_sources = max(1, min(int(max_sources or 5), 10))
    except (TypeError, ValueError):
        max_sources = 5

    cfg = _load_settings()
    try:
        sources = _search(topic, max_sources)
    except Exception as e:
        return {"ok": False, "error": f"web search failed (is there internet?): {e}"}
    if not sources:
        return {"ok": False, "error": "no search results for that topic"}

    for s in sources:
        s["text"] = _fetch_text(s["url"])

    try:
        report, summary = _synthesize(topic, sources, cfg)
    except Exception as e:
        return {"ok": False, "error": f"synthesis failed (is Ollama running?): {e}"}

    report_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
    path = report_path(report_id)
    try:
        _render_pdf(path, topic, summary,
                    report, [{"title": s["title"], "url": s["url"]} for s in sources])
    except Exception as e:
        return {"ok": False, "error": f"PDF generation failed: {e}"}

    entry = {
        "id": report_id,
        "topic": topic,
        "summary": summary,
        "pdf": os.path.basename(path),
        "sources": [{"title": s["title"], "url": s["url"]} for s in sources],
        "created_at": _now(),
    }
    reports = load()
    reports.insert(0, entry)
    save(reports)
    return {"ok": True, "report_id": report_id, "title": topic,
            "summary": summary, "sources": len(sources)}


def list_reports():
    """Past reports (newest first), without the full source text."""
    return {"ok": True, "reports": load()}
