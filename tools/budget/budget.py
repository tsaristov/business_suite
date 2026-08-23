"""Extremely basic budget tracker. Tracks current money, earned, and spent."""

import json
import os
from datetime import date

DATA = os.path.join(os.path.dirname(__file__), "data.json")


def load():
    if not os.path.exists(DATA) or os.path.getsize(DATA) == 0:
        return {"balance": 0.0, "transactions": []}
    with open(DATA) as f:
        return json.load(f)


def save(data):
    with open(DATA, "w") as f:
        json.dump(data, f, indent=2)


def ask_amount():
    while True:
        raw = input("Amount: ").strip()
        try:
            return round(float(raw), 2)
        except ValueError:
            print("Enter a number.")


def add(data, kind):
    amount = ask_amount()
    note = input("Note: ").strip()
    if kind == "earned":
        data["balance"] += amount
    else:
        data["balance"] -= amount
    data["transactions"].append(
        {"type": kind, "amount": amount, "note": note, "date": date.today().isoformat()}
    )
    save(data)
    print(f"Added. Current: ${data['balance']:.2f}")


def view(data):
    if not data["transactions"]:
        print("No transactions yet.")
        return
    for t in data["transactions"]:
        sign = "+" if t["type"] == "earned" else "-"
        print(f"{t['date']}  {sign}{t['amount']:.2f}  {t['note']}")


def main():
    data = load()
    while True:
        print(f"\n--- BUDGET ---\nCurrent: ${data['balance']:.2f}")
        print("1) Add earned\n2) Add spent\n3) View history\n0) Quit")
        choice = input("> ").strip()
        if choice == "1":
            add(data, "earned")
        elif choice == "2":
            add(data, "spent")
        elif choice == "3":
            view(data)
        elif choice == "0":
            break
        else:
            print("Unknown option.")


if __name__ == "__main__":
    main()
