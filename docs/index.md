# Grounded ⚡

**The 0.6ms Reference & Import Integrity Firewall for AI Coding Agents.**

Deterministic, offline, zero dependencies. Runs natively in Python standard library.

---

## What is Grounded?

Modern AI coding agents (Claude Code, Cursor, OpenCode, Devin, Windsurf, Aider) code with incredible speed, but they suffer from **semantic drift and hallucinated APIs**. 

When an agent renames a method in one file, calls a phantom service that does not exist, or invents a plausible-sounding import, developers often wait **3 to 5 minutes** for Docker test suites to fail, wasting tokens and developer time.

**Grounded acts as an instant, sub-millisecond immune system.**

* **0.6ms** in-process scan latency per file.
* **Zero dependencies**: No Rust compiler, no Node.js runtime, no heavy virtualenvs. Pure standard library.
* **Multi-protocol**: Runs via CLI, pre-commit hook, native GitHub Action, Language Server Protocol (LSP 3.17), or Model Context Protocol (MCP).

---

## Quick Example

```console
$ grounded scan ./src
LIE src/app.py:9 [stale-symbol-ref] Comment references `ghost_service` which is not defined here
    claim: `ghost_service()`
    evidence: `ghost_service` is not defined, imported, or used in this file,
              and no definition was found in 84 indexed source files.
    fix: Update the comment to the current name, or remove the reference.

LIE src/views.py:2 [stale-import] `get_user_profile` imported from `src/core.py` but never defined there
    claim: from .core import get_user_profile
    evidence: `src/core.py` exists but defines no `get_user_profile`.
    fix: Check for a rename in `src/core.py` (or a moved submodule).
```

---

## Key Features

* **Code Import Verification (`stale-import`)**: Verifies resolvable imports in Python (`from M import N`) and relative imports in JavaScript/TypeScript (`import { N } from './x'`). Handles re-exports, star chains, and PEP 562 dynamic modules with zero false positives.
* **Scope-Gated Autofix (`grounded fix`)**: Rewrites stale symbol and file references using scope-bounded similarity matching (ratio $\ge 0.75$, same directory only, unambiguous match or no-op).
* **Native LSP 3.17 (`grounded lsp`)**: Drop-in live editor diagnostics and 1-click QuickFix actions for VSCode, Cursor, Zed, and Neovim.
* **Agent Hooks (`grounded init-agent`)**: Scaffolds zero-friction pre-flight rules for Claude Code, Cursor, and Aider.
