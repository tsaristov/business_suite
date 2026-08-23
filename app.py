"""Basic Flask UI for the budget, calendar, checklist, and habits modules.

Reuses each module's load()/save() so data.json stays shared with the CLI scripts.
Run: python app.py  ->  http://127.0.0.1:5000
"""

import importlib.util
import os
from datetime import date, datetime

from flask import Flask, redirect, render_template, request, url_for
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


@app.route("/")
def index():
    tab = request.args.get("tab", "budget")
    return render_template(
        "index.html",
        tab=tab,
        budget=budget.load(),
        events=cal.load(),
        items=checklist.load(),
        habits=habits.load(),
    )


# --- Budget ---
@app.route("/budget/add", methods=["POST"])
def budget_add():
    data = budget.load()
    kind = request.form["kind"]
    amount = round(float(request.form["amount"]), 2)
    note = request.form.get("note", "").strip()
    data["balance"] += amount if kind == "earned" else -amount
    data["transactions"].append(
        {"type": kind, "amount": amount, "note": note, "date": date.today().isoformat()}
    )
    budget.save(data)
    return redirect(url_for("index", tab="budget"))


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
    if 0 <= idx < len(data):
        data[idx]["completions"].append(datetime.now().isoformat(timespec="seconds"))
        habits.save(data)
    return redirect(url_for("index", tab="habits"))


if __name__ == "__main__":
    app.run(debug=True)
