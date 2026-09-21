# Grounded ⚡

**The 0.6ms Reference & Import Integrity Firewall for AI Coding Agents.**

Deterministic, offline, zero dependencies. Runs natively in Python standard library.

---

## What is Grounded?

Modern AI coding agents (Claude Code, Cursor, OpenCode, Devin, Windsurf, Aider) code with incredible speed, but they suffer from **semantic drift and hallucinated APIs**. 

When an agent renames a method in one file, calls a phantom service that does not exist, or invents a plausible-sounding import, the failure often surfaces late: a test run spins up, fails minutes later on an ImportError, and burns a retry loop.

**Grounded checks the reference before you pay for the test run.**

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

## Core concepts & the claim-evidence model

Every finding has the same shape: a **claim** found in the repository, the
**evidence** contradicting it, and a **fix**. A rule stays silent unless
the contradiction is mechanical. There are three outcomes, never two:

* **False** (`lie`, exit 1): the claim contradicts repository state.
  Example: a comment names `get_user_profile()`, but no definition,
  import, or usage of that name exists anywhere indexed.
* **Drift** (`drift`, warning): adjacent evidence disagrees, but the claim
  may describe intent rather than fact. Example: a comment says
  `timeout 30s` next to `TIMEOUT = 60`.
* **Silent**: the claim cannot be decided mechanically (external packages,
  generated files, framework namespaces). Silence is a deliberate verdict,
  not a gap: a verifier that guesses teaches developers to ignore it.

## Key Features

* **Code Import Verification (`stale-import`)**: Verifies resolvable imports in Python (`from M import N`) and relative imports in JavaScript/TypeScript (`import { N } from './x'`). Handles re-exports, star chains, and PEP 562 dynamic modules.
* **Scope-Gated Autofix (`grounded fix`)**: Rewrites stale symbol and file references using scope-bounded similarity matching (ratio 0.75+, same directory only, unambiguous match or no-op).
* **Native LSP 3.17 (`grounded lsp`)**: Drop-in live editor diagnostics and 1-click QuickFix actions for VSCode, Cursor, Zed, and Neovim.
* **Agent Hooks (`grounded init-agent`)**: Scaffolds zero-friction pre-flight rules for Claude Code, Cursor, and Aider.
