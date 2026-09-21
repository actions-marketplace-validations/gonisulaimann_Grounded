# Agents and editors

## One-command setup

```console
grounded init-agent                  # Claude hook + Cursor rule + Aider config
grounded init-agent --cursor         # just .cursor/rules/grounded.mdc
grounded init-agent --skill          # install skill to ~/.claude/skills/grounded
grounded init-agent --skill-project  # install skill to .claude/skills/grounded
```

`init-agent` is idempotent, refuses invalid JSON instead of merging
blindly, and never rewrites an existing Aider config.

## Claude Code

`.claude/settings.json`, runs after every file edit:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [{ "type": "command", "command": "grounded scan . --changed --quiet" }]
      }
    ]
  }
}
```

## Cursor

`.cursor/rules/grounded.mdc` (agent-requested mode: description, no globs).
Plain `.md` files in `.cursor/rules/` are ignored by Cursor; the rule
must be `.mdc` with frontmatter:

```markdown
---
description: Verify code references with grounded before building on edited code
alwaysApply: false
---

After editing source files, run `grounded scan . --changed` and fix
reported lies (dangling function names, missing files) before running
tests or committing.
```

## Aider

`--lint-cmd` accepts filenames and expects a non-zero exit on findings:

```console
aider --lint-cmd "sh -c 'for f; do grounded scan \"$f\" --quiet || exit 1; done' sh"
```

## MCP server

`grounded mcp` serves stdio JSON-RPC for coding agents. Three tools:
`check_path` (scan a path under the server root; paths cannot escape it),
`explain_checker`, and `blast_radius` (definers, importers, and comment
claims for a symbol: ask before renaming).

```json
{
  "mcpServers": {
    "grounded": { "command": "grounded", "args": ["mcp", "--root", "."] }
  }
}
```

## LSP server

`grounded lsp` speaks Language Server Protocol 3.17 over stdio: instant
diagnostics (lie as error, drift as warning, smell as information) plus
quickfix actions for unambiguous renames and path moves. Buffer edits
re-index the file; peer documents re-diagnose; closing reverts to disk.

Neovim:

```lua
vim.api.nvim_create_autocmd("FileType", {
  pattern = { "python", "javascript", "typescript", "go", "c" },
  callback = function()
    vim.lsp.start({ name = "grounded", cmd = { "grounded", "lsp" } })
  end,
})
```

Any editor with a generic LSP client (VS Code, Cursor, Zed, Emacs
eglot) can point at the same command. For a packaged VS Code / Cursor
extension (built from source until marketplace listing), see
[Editor setup](editors.md).

## Agent skill

For the full agent-facing package (entry-point instructions, condensed
references, copyable examples), see the [agent skill](agent-skill.md).
