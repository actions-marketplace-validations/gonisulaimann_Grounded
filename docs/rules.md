# Rules

| ID | Default severity | What it reports |
|---|---|---|
| `stale-symbol-ref` | lie (error) | A comment names a call that resolves nowhere: not defined in the repo, not imported in the file, not used in the file, not a builtin or keyword. Prints rename suggestions when a close match exists. |
| `stale-import` | lie (error) | A resolvable import whose module is missing, or whose name is not defined, re-exported, or a submodule there. Python `from`/`import`, JS/TS relative imports with tsconfig aliases resolved. Guarded, stdlib, and external imports never report. |
| `stale-file-ref` | lie (error) | A comment claims a path inside the repo tree that does not exist. References to other projects, frameworks, template namespaces, and placeholder paths are ignored. |
| `number-drift` | drift (warning) | A comment states a magic number (timeout, port, limit, threshold) that disagrees with adjacent code. |
| `fragile-anchor` | smell (note) | `line 42` anchors, `see above` / `see below` without a symbol, or untracked markers (`HACK`, `XXX`, `FIXME`, `workaround`) with no ticket or expiry condition. |
| `stale-doc-ref` | lie (error), **experimental, opt-in only** | A fenced code example (explicit `python`/`js`/`ts`/`go`/`c` tag) calls a symbol bound nowhere in the example and defined nowhere in the repo. |

## Experimental checkers

`stale-doc-ref` is registered but excluded from every default set: run
it with `--enable stale-doc-ref` (or `enable = ["stale-doc-ref"]`). A
checker graduates to default-on by measured precision, not by age.

Checked, skipped, and why: only fenced blocks with a supported language
tag are read. Bare fences, `console`/`bash` transcripts, data formats,
comment lines inside examples, decorator roots (framework surface), and
any block containing `...` or placeholder names (`foo`, `my_*`,
`<key>`) are skipped. Doc examples are illustrative by default; only a
call with no local binding and no repo-wide definition is reported.

Measurement so far (2026-09-21, v0.12.x codebase as corpus): 60 files
scanned, 9 checkable blocks, **0 findings, 0 false positives** — every
silence individually justified (comment-only blocks, imported names,
bound locals). Recall beyond fixtures is unmeasured: the corpus
contains no known-stale doc example. The bar for default-on is a second
corpus with planted staleness plus a real-world repo showing no new
false-positive class.

## How a rule decides

Imported names, standard library names, parameters, locals, attributes,
docstring field lists (`:param:`, `@param`), and illustrative examples
("For example …") never produce findings. Alias-prefixed JS/TS imports
(`@/`, `~/`) resolve through the nearest `tsconfig.json` (comments and
`extends` supported) or manual `path_aliases`; unresolvable alias targets
report as drift, mappings into `node_modules` stay silent.

## Severities and exit codes

* **`lie` (Error)**: provably false. Exits with code `1` when any finding
  meets `--fail-on` (default `lie`).
* **`drift` (Warning)**: mechanically stale by adjacent evidence.
* **`smell` (Note)**: fragile pattern likely to rot over time.

Exit code `2` means a usage or environment error (bad path, unreadable
baseline or config, unresolvable git base). A typo can never mask drift
with a green build.

## Suppressions

Suppress a single accepted finding where it sits (reviewable, local):

```python
# Calls `legacy_parse()` for old dumps.  # grounded-disable: stale-symbol-ref
```

```js
// Calls `legacyParse()` for old dumps.  // grounded-disable: stale-symbol-ref
```

```go
// Calls `legacyParse()` for old dumps.  // grounded-disable: stale-symbol-ref
```

Unknown checker ids in a marker warn on stderr (`unknown checker id`)
without changing the exit code: a typo never silently disarms a gate.

## Non-goals

Docstring contracts (parameter lists, return sections, raised exceptions)
are covered by [darglint](https://github.com/terrencepreilly/darglint)
and [pydoclint](https://github.com/jsh9/pydoclint) for Python and
[eslint-plugin-jsdoc](https://github.com/gajus/eslint-plugin-jsdoc) for
JavaScript/TypeScript. Commented-out code is covered by
[Ruff ERA001](https://docs.astral.sh/ruff/rules/commented-out-code/).
`grounded explain <id>` points at the right tool for each removed check.

## Limitations

* Unformatted, unverbed name mentions are skipped. This trades recall for
  precision.
* Names imported from anywhere are treated as known elsewhere, including
  cross-module renames.
* Framework namespaces (template paths, URL names) are out of scope.
* JavaScript/TypeScript, Go, and C analysis is syntactic (imports plus
  identifiers), not a full type graph.
* External references stay silent only when recognized (stdlib and POSIX
  names, imports, same-file identifiers).
* Rename suggestions use string similarity only; `grounded fix` applies a
  symbol rename only with exactly one similar, same-directory candidate.
* `grounded fix` rewrites stale file paths only on unambiguous
  same-basename matches in comments (never docstrings, never ties).
