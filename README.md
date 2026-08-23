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

## Project Modules
All of the projects can sync together using the assistant.

An overview of the modules inside the suite, and their use/purpose.

### Assistant
An LLM powered AI assistant with RAG context, tool usage, and agentic capabilities.

### Budget
A budget tracking app that can track what you get and what you pay for, and where it should be allocated.

### Calendar
A calendar app to track your days, and anything you need to be doing throughout it.

### Checklist
A to do list, with multiple diffrent forms of tracking.

### Habits
A daily habit tracking system for diffrent daily habits that should be completed.

## License

TBD
