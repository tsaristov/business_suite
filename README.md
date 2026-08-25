# Custom Business Suite

---

## Overview

Custom Business Suite is a collection of tools and modules to support my day-to-day
business operations. This repository holds the source, configuration, and
documentation for the suite. It's primary use is as a system to help me manage myself.

## Getting Started

```bash
git clone https://github.com/tsaristov/business_suite.git
cd custom_business_suite

python3 -m venv .venv
pip3 install -r requirements.txt

python3 app.py
```

The assistant, knowledge base, research, and image uploads all run on a **local
[Ollama](https://ollama.com)** instance — no API keys. Make sure Ollama is running and
pull the models the suite uses:

```bash
ollama pull llama3.1        # assistant chat / tool-calling + research synthesis
ollama pull nomic-embed-text # knowledge base embeddings (RAG)
ollama pull llava           # image understanding for chat attachments
```

Models are configurable from the **Settings** tab (or via `OLLAMA_MODEL`,
`OLLAMA_VISION_MODEL`, `OLLAMA_EMBED_MODEL`). The **Research** tool additionally needs
internet access when it runs. Email credentials are stored in your OS keychain
(macOS Keychain) via `keyring`, never in the repo.

## Project Structure

```
custom_business_suite/
├── README.md
├── .gitignore
├── app.py                  # Flask web UI (tabs pull each tool's interface.html)
├── requirements.txt
├── templates/
│   └── index.html          # app shell; includes tools/<name>/interface.html per tab
├── assistant/
│   ├── engine.py           # generic function-calling runtime (tool discovery + chat loop)
│   ├── settings.py         # user-tweakable LLM config, system prompt, rules
│   ├── settings.json       # saved settings (git-ignored)
│   ├── interface.html      # chat UI (voice, sessions, file/image attachments)
│   └── settings.html       # Settings tab UI (LLM tweaks + RAG document manager)
└── tools/
    ├── budget/             # budget.py (CLI) · agent.py (AI tool layer) · interface.html · data.json
    ├── calendar/           # calendar.py · agent.py · interface.html · data.json
    ├── checklist/          # checklist.py · agent.py · interface.html · data.json
    ├── habits/             # habits.py · agent.py · interface.html · data.json
    ├── knowledge/          # knowledge.py (RAG) · agent.py · data/ · .storage/ (git-ignored)
    ├── research/           # research.py · agent.py · interface.html · reports/ (git-ignored)
    └── email/              # mailbox.py (IMAP) · agent.py · interface.html · data.json
```

Each tool folder is self-contained: a domain script, an `agent.py` capability layer for
the assistant, and (where it has a tab) an `interface.html` UI fragment for the web app.
Tools persist to their own `data.json` (created on first save, git-ignored). Any
`tools/<name>/agent.py` is auto-discovered by the assistant — no wiring needed.

## Project Tools
The core behind the project is a modular system for business owneres to be able to access
and utilize the tools in place for them to grow themselves and their buisness. This is all
tied together by an AI assistant, that can act as a secretary, managing all of the details,
leaving room for people to grow and expand their own goals.

### Assistant
An LLM powered AI assistant with tool usage and agentic capabilities. It sits on top of
every other tool, reading their data for context and calling their `agent.py` capability
layers to act on your behalf — so you can query and change your budget, calendar,
checklist, habits, knowledge base, research, and email through natural language instead
of each tool's own interface. This is the layer that lets the otherwise independent tools
sync and work together as one system. It also accepts **file and image attachments** in
the chat box: images are described by a local vision model and documents are read for
text, then folded into your message.

### Settings & Knowledge (RAG)
The **Settings** tab is the control panel for the assistant. It lets you tweak the LLM
(chat/vision/embedding models, temperature, retrieval depth, history length), edit the
system prompt, and add extra rules — all applied live, no restart. It also hosts the
**knowledge base**: upload documents (PDF, DOCX, TXT, MD, and more) and they're chunked,
embedded (`nomic-embed-text`), and stored in a local ChromaDB vector store. The assistant
gains a `search_knowledge` tool it calls whenever a question needs your own material.

### Research
Basic web research on demand. Give it a topic and it searches the web (DuckDuckGo, no API
key), reads the top sources, has the model synthesize a structured report, and produces a
**downloadable PDF** plus a short summary. Reachable from the Research tab or by asking
the assistant to "research X and write a report." Requires internet access when it runs.

### Email
Read-only IMAP access to one or more email accounts. Add accounts in the Email tab
(host, port, username, app password) to browse and read mail; the assistant can also list,
read, and search your messages. **Use provider app passwords, not your main password** —
passwords are stored in your OS keychain via `keyring` and never written to the repo.

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

Licensed under the [MIT License](LICENSE) — free to use, modify, and sell,
including commercially. The one requirement: keep the copyright notice, so
credit stays with the author.

Copyright (c) 2026 Daniel Tsaristov
