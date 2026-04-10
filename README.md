# 🧠 Axone CLI

> A modular, AI-powered command-line system — currently in active development 🚧

---

## ⚠️ Status

**Axone is under active development.**
Features may change, break, or evolve rapidly.

> This is a **working prototype / experimental system**, not a stable release.

---

## 🚀 Overview

Axone is a **self-evolving CLI** that can:

* 🧩 Generate Python modules from natural language
* 🤖 Use local AI (via Ollama) when available
* 🔁 Fallback to deterministic generators when offline
* 🔐 Validate code for security (AST-based)
* ⚡ Execute modules dynamically
* 🧠 Learn from usage (basic profiling)

---

## 📦 Versions

| Version          | Description                                       |
| ---------------- | ------------------------------------------------- |
| **axone_go**     | Minimal CLI (no AI, fast & lightweight)           |
| **axone_pro**    | AI-powered with fallback + user profile           |
| **axone_promax** | Full system (caching, logging, advanced features) |

---

## 🛠️ Features

* `/new "<description>"` → generate a module
* `/run <name>` → execute it
* `/fix <name>` → auto-repair with AI
* `/list` → list modules
* `/profile` → view user stats

---

## 💡 Example

```bash
python axone_pro.py

/new "generate a fibonacci sequence"
/run generate_a_fibonacci_sequence
```

---

## 🧪 Tech Stack

* Python 3
* Local AI via Ollama (optional)
* AST parsing for security
* Dynamic module loading

---

## 📁 Project Structure

```
axone/
├── axone_go.py
├── axone_pro.py
├── axone_promax.py
```

> Core system has been intentionally consolidated for simplicity.

---

## ⚙️ Installation

```bash
git clone https://github.com/YOUR-USERNAME/axone
cd axone
python axone_pro.py
```

---

## 🧠 Vision

Axone aims to become:

> A **local-first programmable AI shell**
> that can generate, execute, and evolve code safely.

---

## 🚧 Roadmap (WIP)

* [ ] CLI onboarding experience
* [ ] Visual identity (mascot 👀)
* [ ] Plugin ecosystem
* [ ] Better AI prompts & control
* [ ] Cross-platform packaging

---

## 🤝 Contributing

This project is experimental.
Feedback, ideas, and contributions are welcome.

---

## 📜 License

MIT 

---

## 👀 Note

This project is built fast, iterated fast, and evolving fast.

Expect chaos. That's part of the design.
