# Configuration

## File discovery

With no `--config` flag, grounded reads the first file that exists:

1. `grounded.toml`
2. `.grounded.toml`
3. `pyproject.toml` (only its `[tool.grounded]` table)

With `--config FILE`, only that file is read, and a missing file means
no configuration (not an error). On Python 3.10, files are parsed by a
built-in strict TOML-subset reader; anything outside the subset reads as
unreadable, never half-applied.

## Keys

```toml
# Run only these checkers (unknown ids are dropped; an emptied set
# falls back to all checkers).
enable = ["stale-symbol-ref", "stale-import"]
# enable = [...] and enabled = [...] are synonyms.

# Remove these checkers from the enabled set.
disable = ["fragile-anchor"]

# Minimum severity that fails the run. One of lie, drift, smell, never.
# Anything else falls back to "lie". The --fail-on flag wins over this.
fail_on = "lie"

# Added to the built-in ignore lists, never replacing them.
ignore_dirs = ["docs", "sandbox"]
ignore_files = ["generated.py"]

# JS/TS path aliases, repo-root-relative (tsconfig `paths` are picked
# up automatically per directory; these are the fallback).
# path_aliases = { "@/" = "src/", "~/" = "app/" }
```

## Precedence

Explicit `--config` beats discovery. CLI `--fail-on`, `--enable`, and
`--disable` apply after the file. Ignore lists only ever grow: entries
add to the defaults (`node_modules`, `__pycache__`, lockfiles, and
others stay ignored). A starter file:

```console
grounded init
```
