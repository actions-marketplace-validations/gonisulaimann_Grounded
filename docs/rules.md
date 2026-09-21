# Rules & Checkers

Grounded emits findings with positive, mechanical proof of contradiction.

Supported languages: Python, JavaScript/TypeScript, Go, C.

---

## Active Checkers

| Rule ID | Severity | What It Enforces |
| :--- | :--- | :--- |
| `stale-import` | `lie` (Error) | Resolvable imports (`from M import N` in Python; relative `import {N} from './x'` in JS/TS) where the target module is missing or `N` is not defined, re-exported, or a submodule there. |
| `stale-symbol-ref` | `lie` (Error) | A comment names a call (`foo()` or `` `foo()` ``) that resolves nowhere: not defined in the repo, not imported in the file, not used in the file, not a builtin or keyword. |
| `stale-file-ref` | `lie` (Error) | A comment claims a file path inside this repo's tree that does not exist (namespace- and placeholder-aware). |
| `number-drift` | `drift` (Warning) | A comment states a magic number (timeout, port, limit, threshold) that disagrees with adjacent code. |
| `fragile-anchor` | `smell` (Note) | Line anchors (`line 42`), `see above` / `see below` without a symbol, or untracked markers (`HACK`, `XXX`, `FIXME`, `workaround`) with no ticket or expiry condition. |

---

## Severities

* **`lie` (Error)**: Provably false. The reference or import claims a contract that does not exist in the codebase. Exits with code `1`.
* **`drift` (Warning)**: Mechanically stale by adjacent evidence.
* **`smell` (Note)**: Fragile pattern likely to rot over time.

Exit codes: `0` clean, `1` a finding at or above `--fail-on` (default
`lie`), `2` a usage or environment error (bad path, unreadable baseline
or config, unresolvable git base). A typo can never mask drift with a
green build: misconfiguration fails loudly.

---

## Suppressions

To suppress an accepted finding inline, place a comment marker directly on the offending line:

### Python
```python
# Calls legacy_dump() for historical archives  # grounded-disable: stale-symbol-ref
```

### JavaScript / TypeScript
```javascript
// Calls legacyDump() for historical archives  // grounded-disable: stale-symbol-ref
```

To suppress all checkers on a line:
```python
# Temporary patch  # grounded-disable: all
```

Unknown checker ids in a marker warn on stderr (`unknown checker id`)
without changing the exit code: a typo never silently disarms a gate.
