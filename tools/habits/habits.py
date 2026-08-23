"""Extremely basic habit tracker. Create habits, mark complete with date+time."""

import json
import os
from datetime import date, datetime, timedelta

DATA = os.path.join(os.path.dirname(__file__), "data.json")


def completion_dates(habit):
    """Set of YYYY-MM-DD strings the habit was completed on (dedupes same-day)."""
    return {str(c)[:10] for c in habit.get("completions", [])}


def completed_today(habit, today=None):
    """True if the habit already has a completion dated today."""
    today = (today or date.today()).isoformat()
    return today in completion_dates(habit)


def mark_complete(habit, now=None):
    """Record one completion for today. No-op if already done today.

    This is the single source of truth for the once-daily rule: spam clicks
    (UI) or repeated CLI entries all funnel here and get rejected.
    Returns True if a new completion was recorded, else False.
    """
    if completed_today(habit):
        return False
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    habit.setdefault("completions", []).append(stamp)
    return True


def streak(habit, today=None):
    """Consecutive-day streak ending today (or yesterday if not yet done today)."""
    days = completion_dates(habit)
    if not days:
        return 0
    today = today or date.today()
    start = today if today.isoformat() in days else today - timedelta(days=1)
    count, cur = 0, start
    while cur.isoformat() in days:
        count += 1
        cur -= timedelta(days=1)
    return count


def load():
    if not os.path.exists(DATA) or os.path.getsize(DATA) == 0:
        return []
    with open(DATA) as f:
        return json.load(f)


def save(habits):
    with open(DATA, "w") as f:
        json.dump(habits, f, indent=2)


def add(habits):
    name = input("Habit name: ").strip()
    if not name:
        print("Name required.")
        return
    habits.append({"name": name, "completions": []})
    save(habits)
    print("Habit added.")


def view(habits):
    if not habits:
        print("No habits yet.")
        return
    for i, h in enumerate(habits):
        last = h["completions"][-1] if h["completions"] else "never"
        print(f"{i}) {h['name']}  ({len(h['completions'])} done, last: {last})")


def complete(habits):
    view(habits)
    if not habits:
        return
    raw = input("Complete which #: ").strip()
    try:
        habit = habits[int(raw)]
    except (ValueError, IndexError):
        print("Invalid number.")
        return
    if mark_complete(habit):
        save(habits)
        print(f"Marked complete ({date.today().isoformat()}).")
    else:
        print("Already done today.")


def main():
    habits = load()
    while True:
        print("\n--- HABITS ---")
        print("1) Add habit\n2) Mark complete\n3) View habits\n0) Quit")
        choice = input("> ").strip()
        if choice == "1":
            add(habits)
        elif choice == "2":
            complete(habits)
        elif choice == "3":
            view(habits)
        elif choice == "0":
            break
        else:
            print("Unknown option.")


if __name__ == "__main__":
    main()
