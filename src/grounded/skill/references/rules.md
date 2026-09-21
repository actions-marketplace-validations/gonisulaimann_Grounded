# Rules reference

Condensed checker semantics for agents. Full prose lives in the
[rules documentation](https://grounded.readthedocs.io/en/latest/rules/).

| ID | Severity | Reports | Never reports |
|---|---|---|---|
| `stale-symbol-ref` | lie | comment names a call resolving nowhere | builtins, keywords, imports, params, locals, Sphinx/JSDoc fields |
| `stale-import` | lie | resolvable import to a missing module or name | stdlib, deps, guarded imports, star imports, dunders |
| `stale-file-ref` | lie | comment claims an in-repo path that does not exist | external paths, placeholders, examples, ticketed history |
| `number-drift` | drift | magic number disagreeing with adjacent code | matching numbers, example sentences |
| `fragile-anchor` | smell | line anchors, bare see-above/below, untracked workarounds | ticketed or conditioned markers |
| `stale-doc-ref` | lie, opt-in only (`--enable stale-doc-ref`) | fenced code example calls a symbol defined nowhere in the repo | bare fences, console blocks, `...` blocks, placeholders, bound locals |

Exit codes: `0` clean, `1` a finding at or above `--fail-on` (default
`lie`), `2` usage or environment error.

Suppress one accepted finding where it sits:

```python
# Calls `legacy_parse()` for old dumps.  # grounded-disable: stale-symbol-ref
```

Unknown ids in a marker warn on stderr without changing the exit code.
