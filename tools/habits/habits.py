"""Extremely basic habit tracker. Create habits, mark complete with date+time."""

import json
import os
from datetime import datetime

DATA = os.path.join(os.path.dirname(__file__), "data.json")


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
        stamp = datetime.now().isoformat(timespec="seconds")
        habits[int(raw)]["completions"].append(stamp)
        save(habits)
        print(f"Marked complete at {stamp}.")
    except (ValueError, IndexError):
        print("Invalid number.")


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
