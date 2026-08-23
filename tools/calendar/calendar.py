"""Extremely basic calendar. Tracks events: title, description, priority, date, duration."""

import json
import os

DATA = os.path.join(os.path.dirname(__file__), "data.json")


def load():
    if not os.path.exists(DATA) or os.path.getsize(DATA) == 0:
        return []
    with open(DATA) as f:
        return json.load(f)


def save(events):
    with open(DATA, "w") as f:
        json.dump(events, f, indent=2)


def add(events):
    title = input("Title: ").strip()
    if not title:
        print("Title required.")
        return
    description = input("Details/description: ").strip()
    priority = input("Priority (low/med/high): ").strip()
    event_date = input("Date (YYYY-MM-DD): ").strip()
    event_time = input("Time (HH:MM, blank = all-day): ").strip()
    duration = input("Duration (e.g. 2h): ").strip()
    events.append(
        {
            "title": title,
            "description": description,
            "priority": priority,
            "date": event_date,
            "time": event_time,
            "duration": duration,
        }
    )
    save(events)
    print("Event added.")


def view(events):
    if not events:
        print("No events yet.")
        return
    for i, e in enumerate(sorted(events, key=lambda x: x["date"])):
        when = e["date"] + (f" {e['time']}" if e.get("time") else "")
        print(
            f"{i}) {when}  {e['title']}  [{e['priority']}]  {e['duration']}\n"
            f"    {e['description']}"
        )


def delete(events):
    view(events)
    if not events:
        return
    raw = input("Delete which #: ").strip()
    try:
        idx = int(raw)
        ordered = sorted(events, key=lambda x: x["date"])
        events.remove(ordered[idx])
        save(events)
        print("Deleted.")
    except (ValueError, IndexError):
        print("Invalid number.")


def main():
    events = load()
    while True:
        print("\n--- CALENDAR ---")
        print("1) Add event\n2) View events\n3) Delete event\n0) Quit")
        choice = input("> ").strip()
        if choice == "1":
            add(events)
        elif choice == "2":
            view(events)
        elif choice == "3":
            delete(events)
        elif choice == "0":
            break
        else:
            print("Unknown option.")


if __name__ == "__main__":
    main()
