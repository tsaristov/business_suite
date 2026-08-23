# Custom Business Suite

A custom suite of business applications for my own personal use.

## Overview

Custom Business Suite is a collection of tools and modules to support my day-to-day
business operations. This repository holds the source, configuration, and
documentation for the suite. It's primary use is as a system to help me manage myself.

## Getting Started

```bash
git clone https://github.com/tsaristov/business_suite.git
cd custom_business_suite
```

_Setup and build instructions to be added as the project takes shape._

## Project Structure

```
custom_business_suite/
├── README.md
├── app.py                  # Flask web UI (tabs pull each tool's interface.html)
├── requirements.txt
├── templates/
│   └── index.html          # app shell; includes tools/<name>/interface.html per tab
├── assistant/
└── tools/
    ├── budget/             # budget.py (CLI) · agent.py (AI tool layer) · interface.html
    ├── calendar/
    ├── checklist/
    └── habits/
```

Each tool folder is self-contained: a CLI script, an `agent.py` capability layer for
the assistant, an `interface.html` UI fragment for the web app, and its own
`data.json` store (shared across all three).

## Project Tools
All of the projects can sync together using the assistant.

### Assistant
An LLM powered AI assistant with RAG context, tool usage, and agentic capabilities. It
sits on top of every other tool, reading their data for context and calling their
`agent.py` capability layers to act on your behalf — so you can query and change your
budget, calendar, checklist, and habits through natural language instead of each tool's
own interface. This is the layer that lets the otherwise independent tools sync and work
together as one system.

### Budget
A budget tracking app that can track what you get and what you pay for, and where it
should be allocated. It records categorized income and expense transactions, enforces
per-category spending limits (either a fixed amount or a percent of income), tracks bills
with due-date reminders, and follows savings goals with target amounts and timelines. It
also runs analytics over your history so you can see where money actually goes.

### Calendar
A calendar app to track your days, and anything you need to be doing throughout it. Each
event carries a title, description, priority, date, time (or all-day), and duration, giving
you a simple timeline of what's scheduled and how important it is.

### Checklist
A to do list, with multiple diffrent forms of tracking. Items hold a name, short
description, and priority, and can be marked done, so you can keep both quick tasks and
longer-running items organized by how much they matter.

### Habits
A daily habit tracking system for diffrent daily habits that should be completed. It
records timestamped completions, enforces a once-per-day rule (repeat clicks or entries
are ignored), and computes consecutive-day streaks so you can see how consistently each
habit is kept.

## License

TBD
