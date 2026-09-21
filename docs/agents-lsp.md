# Editor & Agent Setup

Grounded speaks standard JSON-RPC 2.0 protocols over `stdio` to integrate directly into AI agent loops and modern code editors.

---

## 1-Command Agent Setup (`init-agent`)

Scaffold drop-in pre-flight rules for your coding agents:

```bash
grounded init-agent
```

This automatically writes:
* `.claude/settings.json` (Post-tool execution lint hook for Claude Code)
* `.cursor/rules/grounded.mdc` (Pre-commit and edit rules for Cursor)
* `.aider.conf.yml` (Native lint command for Aider)

---

## Language Server Protocol (LSP 3.17)

Run Grounded as a live Language Server for any editor:

```bash
grounded lsp
```

### VSCode & Cursor Setup

Use any generic LSP client and point it at the `grounded lsp` command over
stdio. The task snippet in the README (problem matcher included) works
without extensions; for live squigglies, any client that launches a
stdio server command will do.

Capabilities provided:
* `textDocument/publishDiagnostics`: Live red squigglies on hallucinated imports and stale comments as you type.
* `textDocument/codeAction`: 1-click QuickFix actions to heal broken references and imports.

---

## Model Context Protocol (MCP)

Grounded exposes a zero-dependency MCP server over `stdio` for agent runtimes:

```json
{
  "mcpServers": {
    "grounded": {
      "command": "grounded",
      "args": ["mcp", "--root", "."]
    }
  }
}
```

### Tools Provided:
* `check_path`: Scans a specific file or directory under the root and returns structured findings. Root-confined for security.
* `explain_checker`: Explains what an individual checker proves and remediation steps.
* `blast_radius`: Definers, importers, and comment claims for a symbol. Ask before renaming.
