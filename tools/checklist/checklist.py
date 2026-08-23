"""Extremely basic checklist. Items with description, priority, mark done."""

import json
import os

DATA = os.path.join(os.path.dirname(__file__), "data.json")


def load():
    if not os.path.exists(DATA) or os.path.getsize(DATA) == 0:
        return []
    with open(DATA) as f:
        return json.load(f)


def save(items):
    with open(DATA, "w") as f:
        json.dump(items, f, indent=2)


def add(items):
    name = input("Item: ").strip()
    if not name:
        print("Item required.")
        return
    description = input("Short description: ").strip()
    priority = input("Priority (low/med/high): ").strip()
    items.append(
        {"item": name, "description": description, "priority": priority, "done": False}
    )
    save(items)
    print("Item added.")


def view(items):
    if not items:
        print("List empty.")
        return
    for i, it in enumerate(items):
        box = "x" if it["done"] else " "
        print(f"{i}) [{box}] {it['item']}  [{it['priority']}]  {it['description']}")


def mark_done(items):
    view(items)
    if not items:
        return
    raw = input("Mark which #: ").strip()
    try:
        items[int(raw)]["done"] = True
        save(items)
        print("Marked done.")
    except (ValueError, IndexError):
        print("Invalid number.")


def delete(items):
    view(items)
    if not items:
        return
    raw = input("Delete which #: ").strip()
    try:
        removed = items.pop(int(raw))
        save(items)
        print(f"Deleted: {removed['item']}")
    except (ValueError, IndexError):
        print("Invalid number.")


def main():
    items = load()
    while True:
        print("\n--- CHECKLIST ---")
        print("1) Add item\n2) View list\n3) Mark done\n4) Delete item\n0) Quit")
        choice = input("> ").strip()
        if choice == "1":
            add(items)
        elif choice == "2":
            view(items)
        elif choice == "3":
            mark_done(items)
        elif choice == "4":
            delete(items)
        elif choice == "0":
            break
        else:
            print("Unknown option.")


if __name__ == "__main__":
    main()
