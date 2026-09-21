# Grounded for Cursor & VS Code

**The sub-millisecond reference integrity firewall that stops AI coding agents and hallucinations directly in your editor.**

Catch hallucinated function calls, phantom imports, and stale file claims in **0.6 milliseconds** directly inside your editor. When Claude Code, Cursor Composer, Windsurf, or GitHub Copilot edits your code, Grounded flags broken references as red squigglies *before* you run tests or commit.

---

### Key Capabilities

* **⚡ Sub-Millisecond Diagnostics**: AST parsing in 0.6ms. Zero CPU lag or editor sluggishness.
* **🛡️ AI Agent Hallucination Shield**: Catches when an LLM claims to call a function, import a module, or reference a file that does not actually exist in your repository.
* **🔧 1-Click QuickFix Actions**: Instant editor code actions rewrite stale function calls and moved file paths with a single click.
* **🌐 Multi-Language Native**: Full syntax coverage for Python, JavaScript, TypeScript (including JSX/TSX React), Go, and C.
* **🔒 Zero Telemetry & 100% Offline**: Deterministic verification running locally over Language Server Protocol (LSP 3.17). No external cloud dependencies or API keys required.

---

## Quick Setup

1. Install the **Grounded** extension in Cursor, Windsurf, or VS Code.
2. Install the lightweight `grounded` engine:
   ```bash
   pip install grounded-lint
   # or with Homebrew:
   brew install gonisulaimann/tap/grounded
   ```
   *Tip: If `grounded` is not on your PATH, the extension will ask to run via `uvx` automatically with zero manual installation required.*

---

## Diagnostic Tiers

Grounded categorizes reference findings into three clean tiers:

| Severity | What It Catches | Editor Indicator |
|---|---|---|
| **LIE** | Provably false claims (non-existent functions, missing module imports, dead files) | 🔴 Red Squiggly (Error) |
| **DRIFT** | Numeric values or signatures that disagree with adjacent code implementation | 🟡 Yellow Squiggly (Warning) |
| **SMELL** | Fragile anchors (`line 42`) or unlinked workaround comments | 🔵 Blue Squiggly (Info) |

---

## Extension Settings

Configure Grounded via your `.vscode/settings.json` or Extension Settings UI:

| Setting | Default | Description |
|---|---|---|
| `grounded.serverPath` | `""` | Custom path to the `grounded` binary (e.g. inside a virtual environment). |
| `grounded.allowUvx` | `false` | Always launch the LSP server via `uvx --from grounded-lint grounded lsp` without prompting. |

---

## Community & Resources

* **GitHub Repository:** [gonisulaimann/Grounded](https://github.com/gonisulaimann/Grounded)
* **Official Documentation:** [grounded.readthedocs.io](https://grounded.readthedocs.io)
* **Report Issues:** [GitHub Issues](https://github.com/gonisulaimann/Grounded/issues)
* **License:** MIT
