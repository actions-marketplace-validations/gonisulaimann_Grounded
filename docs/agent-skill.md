# Grounded Agent Skill

While the [MCP server](agents.md#mcp-server) gives agents tools to check
references, the **grounded agent skill** teaches coding agents the workflow
itself, so the code they write stays verifiable instead of drifting from
the repository.

The skill follows the [AgentSkills](https://agentskills.io/specification)
specification, so it loads in Claude Code and other spec-compatible
agents. It lives in the [agent-skill](https://github.com/gonisulaimann/Grounded/tree/main/agent-skill)
directory under the name `grounded`.

## Installation

One command installs the skill for every project (no paths to memorize,
directories are created for you):

```console
grounded init-agent --skill
```

This copies the skill to `~/.claude/skills/grounded/`. For one repo only:

```console
grounded init-agent --skill-project   # .claude/skills/grounded/
```

Existing files are kept unless you pass `--force`; `--dry-run` previews.
Bare `grounded init-agent` (Claude hook + Cursor rule + Aider config)
never writes outside the repo: installing to your home directory always
requires the explicit `--skill` opt-in.

Manual fallback (same result, if the binary is unavailable):

```console
cp -r agent-skill ~/.claude/skills/grounded
```

## ClawHub registry (maintainer)

The skill directory is already a publishable ClawHub package (`SKILL.md`
plus supporting files, no build step). Publishing needs a registry
account, so it is a manual maintainer step, not CI:

```console
npm i -g clawhub
clawhub login
clawhub skill publish ./agent-skill --slug grounded --name "Grounded" \
  --version "$(grounded --version | awk '{print $2}')" --tags latest
```

After the release clears review, users install with
`openclaw skills install @<owner>/grounded` and the badge below goes on
the README. No badge is added before the listing is live.

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
