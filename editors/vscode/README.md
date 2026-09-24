# Grounded for Cursor & VS Code

The reference integrity firewall that stops AI coding agents and hallucinations directly in your editor.

Catch hallucinated function calls, phantom imports, and stale file claims with a **0.6 ms per-file re-check** directly inside your editor. When Claude Code, Cursor Composer, Windsurf, or GitHub Copilot edits your code, Grounded flags broken references as red squigglies *before* you run tests or commit.

* **Sub-millisecond diagnostics:** AST parsing in 0.6ms. Zero CPU lag or editor sluggishness.
* **AI agent hallucination shield:** Catches when an LLM claims to call a function, import a module, or reference a file that does not exist in your repository.
* **1-click quickfix actions:** Instant editor code actions rewrite stale function calls and moved file paths with a single click.
* **Multi-language native:** Full syntax coverage for Python, JavaScript, TypeScript (including JSX/TSX React), Go, and C.
* **Zero telemetry and offline:** Deterministic verification running locally over Language Server Protocol (LSP 3.17). No external cloud dependencies or API keys required.

## New to Grounded?

Visit [grounded.readthedocs.io](https://grounded.readthedocs.io) to get started with Grounded.

## Requirements

* VS Code 1.85.0 or higher (or Cursor, Windsurf, VSCodium)
* Python 3.10 or higher with `grounded-lint` installed (`pip install grounded-lint` or `brew install gonisulaimann/tap/grounded`), or `uvx` available

## Extension Settings

Configure Grounded via `.vscode/settings.json` or Extension Settings:

| Setting | Default | Description |
|---|---|---|
| `grounded.serverPath` | `""` | Custom path to the `grounded` binary (e.g. inside a virtual environment). |
| `grounded.allowUvx` | `false` | Always launch the LSP server via `uvx --from grounded-lint grounded lsp` without prompting. |

## Docs

See our [documentation](https://grounded.readthedocs.io) for more information on using the extension, rules, and CLI options.
