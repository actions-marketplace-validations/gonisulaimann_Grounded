# Grounded

Find dangling references in code comments: functions that no longer
exist, files that are not there, imports that cannot resolve.

```console
$ grounded scan ./src
LIE src/app.py:9 [stale-symbol-ref] Comment references `ghost_service` which is not defined here
    claim: `ghost_service()`
    evidence: `ghost_service` is not defined, imported, or used in this file,
              and no definition was found in 84 indexed source files.
    fix: Update the comment to the current name, or remove the reference.
```

Every finding carries the same shape: the **claim** found in the
repository, the **evidence** contradicting it, and a **fix**. A rule stays
silent unless the contradiction is mechanical. There are three outcomes,
never two:

* **False** (`lie`, exit 1): the claim contradicts repository state.
* **Drift** (`drift`, warning): adjacent evidence disagrees, but the claim
  may describe intent rather than fact.
* **Silent**: the claim cannot be decided mechanically (external packages,
  generated files, framework namespaces). Silence is a deliberate verdict,
  not a gap: a verifier that guesses teaches developers to ignore it.

Deterministic, offline, zero dependencies. Python, JavaScript/TypeScript,
Go, and C.

## Start here

* [Installation](installation.md): brew, pip, uvx, source.
* [Rules](rules.md): the five checkers, severities, suppressions.
* [Agents](agents.md): Claude/Cursor/Aider setup, MCP server, LSP server,
  and the [agent skill](agent-skill.md).
* [Benchmarks](benchmarks.md): measured timings and the harness that
  produces them.

## For coding agents

If you are an agent reading this before trusting repository claims, start
with the [agent skill](agent-skill.md): it teaches when to verify a
reference, which tool answers each question, and what a finding means.
