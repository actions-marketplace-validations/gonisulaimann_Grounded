# Grounded Agent Skill

While the [MCP server](agents.md#mcp-server) gives agents tools to check
references, the **grounded agent skill** teaches coding agents the workflow
itself, so the code they write stays verifiable instead of drifting from
the repository.

The skill follows the [AgentSkills](https://agentskills.io/specification)
specification, so it loads in Claude Code and other spec-compatible
agents. It lives in the [agent-skill](https://github.com/gonisulaimann/Grounded/tree/main/agent-skill)
directory under the name `grounded-official`.

## Installation

Copy the directory into your agent's skills location (Claude Code reads
`~/.claude/skills/`):

```console
cp -r agent-skill ~/.claude/skills/grounded-official
```

Or generate the per-project wiring instead and skip the skill entirely:

```console
grounded init-agent   # Claude hook + Cursor rule + Aider config
```

## What's inside

* **`SKILL.md`**: the entry point the agent reads first. When to verify a
  reference, which tool answers each question, and what each finding
  means. Includes the non-goals: docstring contracts belong to darglint,
  commented-out code belongs to Ruff.
* **`references/rules.md`**: condensed rule table (the five checkers,
  severities, exit codes). Loaded when the agent needs exact semantics.
* **`references/commands.md`**: CLI surface (`scan`, `fix`, `impact`,
  `baseline`, `mcp`, `lsp`) with the agent-loop contracts (exit codes,
  single-file checks, dry runs).
* **`examples/`**: copyable sessions — a stale import found after a
  rename, a `blast_radius` query before renaming, a `fix --dry-run`
  preview.

## Rule of the skill

Verify, then trust. Before building on a symbol, path, or import the
repository did not show you in this session, ask grounded first: a
`check_path` call costs milliseconds, a hallucinated call costs a retry
loop.
