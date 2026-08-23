"""Basic Flask UI for the budget, calendar, checklist, and habits modules.

Reuses each module's load()/save() so data.json stays shared with the CLI scripts.
Run: python app.py  ->  http://127.0.0.1:5000
"""

import importlib.util
import os
import re
from datetime import date, datetime, timedelta

from flask import Flask, jsonify, redirect, render_template, request, url_for
from jinja2 import ChoiceLoader, FileSystemLoader

BASE = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.join(BASE, "tools")


def _load_module(name, *parts):
    """Load a module by file path under a unique name (avoids stdlib shadowing)."""
    path = os.path.join(TOOLS_DIR, *parts)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


budget = _load_module("budget_mod", "budget", "budget.py")
cal = _load_module("calendar_mod", "calendar", "calendar.py")
checklist = _load_module("checklist_mod", "checklist", "checklist.py")
habits = _load_module("habits_mod", "habits", "habits.py")

app = Flask(__name__)

# Let templates/index.html include each tool's own UI at <tool>/interface.html
# (e.g. {% include 'budget/interface.html' %}), resolved from the tools/ dir.
app.jinja_loader = ChoiceLoader([app.jinja_loader, FileSystemLoader(TOOLS_DIR)])


def _budget_context():
    """Everything the budget interface needs: raw data + analytics for the sub-tabs."""
    data = budget.load()
    return {
        "data": data,
        "overview": budget.overview(data),
        "categories": {"income": data["income_sources"],
                       "expense": data["expense_categories"]},
        "limits": budget.limit_status(data),
        "bills": budget.bill_status(data),
        "goals": budget.goal_status(data),
        "by_category": budget.spending_by_category(data),
        "series": budget.monthly_series(data, 6),
    }


def _habits_context(days=49):
    """Habit list enriched for the UI: today-status, streak, and a completion
    heatmap grid over the last `days` days (49 = 7 weeks, renders 7 per row)."""
    today = date.today()
    # Newest first: today lands top-left, oldest day bottom-right.
    window = [today - timedelta(days=i) for i in range(days)]
    out = []
    for h in habits.load():
        done = habits.completion_dates(h)
        out.append({
            "name": h["name"],
            "total": len(h.get("completions", [])),
            "done_today": today.isoformat() in done,
            "streak": habits.streak(h, today),
            "last": h["completions"][-1] if h.get("completions") else None,
            "grid": [{"date": d.isoformat(), "done": d.isoformat() in done}
                     for d in window],
        })
    return out


@app.route("/")
def index():
    tab = request.args.get("tab", "budget")
    return render_template(
        "index.html",
        tab=tab,
        budget=_budget_context(),
        events=cal.load(),
        items=checklist.load(),
        habits=_habits_context(),
    )


# --- Budget --- (all logic lives in tools/budget/budget.py; routes just delegate)
def _to_budget():
    return redirect(url_for("index", tab="budget"))


@app.route("/budget/add", methods=["POST"])
def budget_add():
    budget.add_transaction(
        request.form["kind"],
        request.form.get("amount", 0),
        request.form.get("category", ""),
        request.form.get("note", ""),
    )
    return _to_budget()


@app.route("/budget/category/add", methods=["POST"])
def budget_category_add():
    budget.add_category(request.form["kind"], request.form.get("name", ""))
    return _to_budget()


@app.route("/budget/limit", methods=["POST"])
def budget_limit():
    budget.set_limit(
        request.form.get("category", ""),
        request.form.get("type", "percent"),
        request.form.get("value", 0),
    )
    return _to_budget()


@app.route("/budget/limit/remove", methods=["POST"])
def budget_limit_remove():
    budget.remove_limit(request.form.get("category", ""))
    return _to_budget()


@app.route("/budget/bill/add", methods=["POST"])
def budget_bill_add():
    budget.add_bill(
        request.form.get("name", ""),
        request.form.get("amount", 0),
        request.form.get("due_day", 1),
        request.form.get("category", ""),
        request.form.get("note", ""),
    )
    return _to_budget()


@app.route("/budget/bill/remove/<int:idx>", methods=["POST"])
def budget_bill_remove(idx):
    budget.remove_bill(idx)
    return _to_budget()


@app.route("/budget/goal/add", methods=["POST"])
def budget_goal_add():
    budget.add_goal(
        request.form.get("name", ""),
        request.form.get("target", 0),
        request.form.get("target_date", ""),
        request.form.get("saved", 0) or 0,
    )
    return _to_budget()


@app.route("/budget/goal/contribute/<int:idx>", methods=["POST"])
def budget_goal_contribute(idx):
    budget.contribute_goal(idx, request.form.get("amount", 0))
    return _to_budget()


@app.route("/budget/goal/remove/<int:idx>", methods=["POST"])
def budget_goal_remove(idx):
    budget.remove_goal(idx)
    return _to_budget()


# --- Calendar ---
@app.route("/calendar/add", methods=["POST"])
def calendar_add():
    events = cal.load()
    events.append(
        {
            "title": request.form["title"].strip(),
            "description": request.form.get("description", "").strip(),
            "priority": request.form.get("priority", "").strip(),
            "date": request.form.get("date", "").strip(),
            "duration": request.form.get("duration", "").strip(),
        }
    )
    cal.save(events)
    return redirect(url_for("index", tab="calendar"))


@app.route("/calendar/delete/<int:idx>", methods=["POST"])
def calendar_delete(idx):
    events = cal.load()
    if 0 <= idx < len(events):
        events.pop(idx)
        cal.save(events)
    return redirect(url_for("index", tab="calendar"))


# --- Calendar JSON API (feeds the FullCalendar UI in calendar/interface.html) ---
_PRIORITIES = ("low", "med", "high")
_PRIORITY_COLORS = {"low": "#4caf50", "med": "#2196f3", "high": "#e53935"}


def _duration_minutes(duration, default=60):
    """Parse free-text duration -> minutes. Handles '2h', '30m', '1h30m', '90', '1.5h'."""
    s = str(duration or "").strip().lower()
    if not s:
        return default
    total = 0.0
    for value, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([hm]?)", s):
        num = float(value)
        total += num * 60 if unit == "h" else num  # bare number = minutes
    return int(total) if total > 0 else default


def _norm_priority(value, default="med"):
    p = str(value or default).strip().lower()
    return p if p in _PRIORITIES else default


def _event_to_fc(idx, e):
    """Map a stored event dict to a FullCalendar event object (start/end derived)."""
    date_str = str(e.get("date") or "").strip()
    time_str = str(e.get("time") or "").strip()
    priority = _norm_priority(e.get("priority"))
    color = _PRIORITY_COLORS[priority]
    fc = {
        "id": str(idx),
        "title": e.get("title", ""),
        "allDay": not bool(time_str),
        "backgroundColor": color,
        "borderColor": color,
        "extendedProps": {
            "description": e.get("description", ""),
            "priority": priority,
            "duration": e.get("duration", ""),
            "time": time_str,
            "date": date_str,
        },
    }
    if not date_str:
        return fc  # undated event: still returned, just not placed on the grid
    if time_str:
        start = f"{date_str}T{time_str}"
        fc["start"] = start
        try:
            end_dt = datetime.fromisoformat(start) + timedelta(
                minutes=_duration_minutes(e.get("duration"))
            )
            fc["end"] = end_dt.isoformat()
        except ValueError:
            pass
    else:
        fc["start"] = date_str
    return fc


_EVENT_FIELDS = ("title", "description", "priority", "date", "time", "duration")


@app.route("/calendar/events", methods=["GET"])
def calendar_events():
    return jsonify([_event_to_fc(i, e) for i, e in enumerate(cal.load())])


@app.route("/calendar/events", methods=["POST"])
def calendar_event_create():
    payload = request.get_json(silent=True) or {}
    title = str(payload.get("title", "")).strip()
    if not title:
        return {"ok": False, "error": "title is required"}, 400
    events = cal.load()
    events.append(
        {
            "title": title,
            "description": str(payload.get("description", "")).strip(),
            "priority": _norm_priority(payload.get("priority")),
            "date": str(payload.get("date", "")).strip(),
            "time": str(payload.get("time", "")).strip(),
            "duration": str(payload.get("duration", "")).strip(),
        }
    )
    cal.save(events)
    return {"ok": True, "id": len(events) - 1}


@app.route("/calendar/events/<int:idx>", methods=["PUT"])
def calendar_event_update(idx):
    payload = request.get_json(silent=True) or {}
    events = cal.load()
    if not 0 <= idx < len(events):
        return {"ok": False, "error": "no such event"}, 404
    event = events[idx]
    for field in _EVENT_FIELDS:
        if field in payload:
            event[field] = str(payload[field]).strip()
    if "priority" in payload:
        event["priority"] = _norm_priority(event.get("priority"))
    cal.save(events)
    return {"ok": True, "id": idx}


@app.route("/calendar/events/<int:idx>", methods=["DELETE"])
def calendar_event_delete(idx):
    events = cal.load()
    if not 0 <= idx < len(events):
        return {"ok": False, "error": "no such event"}, 404
    events.pop(idx)
    cal.save(events)
    return {"ok": True}


# --- Checklist ---
@app.route("/checklist/add", methods=["POST"])
def checklist_add():
    items = checklist.load()
    items.append(
        {
            "item": request.form["item"].strip(),
            "description": request.form.get("description", "").strip(),
            "priority": request.form.get("priority", "").strip(),
            "done": False,
        }
    )
    checklist.save(items)
    return redirect(url_for("index", tab="checklist"))


@app.route("/checklist/done/<int:idx>", methods=["POST"])
def checklist_done(idx):
    items = checklist.load()
    if 0 <= idx < len(items):
        items[idx]["done"] = not items[idx]["done"]
        checklist.save(items)
    return redirect(url_for("index", tab="checklist"))


@app.route("/checklist/delete/<int:idx>", methods=["POST"])
def checklist_delete(idx):
    items = checklist.load()
    if 0 <= idx < len(items):
        items.pop(idx)
        checklist.save(items)
    return redirect(url_for("index", tab="checklist"))


# --- Habits ---
@app.route("/habits/add", methods=["POST"])
def habits_add():
    data = habits.load()
    data.append({"name": request.form["name"].strip(), "completions": []})
    habits.save(data)
    return redirect(url_for("index", tab="habits"))


@app.route("/habits/complete/<int:idx>", methods=["POST"])
def habits_complete(idx):
    data = habits.load()
    # mark_complete enforces the once-daily rule, so spam clicks are no-ops.
    if 0 <= idx < len(data) and habits.mark_complete(data[idx]):
        habits.save(data)
    return redirect(url_for("index", tab="habits"))


if __name__ == "__main__":
    app.run(debug=True)
