# Architecture

How grounded is put together, as implemented (not as marketed).

## Pipeline

```text
collect_files → read texts once → RepoIndex → parse + check per file
    → suppressions → delta filters (changed/baseline) → reporters
```

* **Collect** (`scanner.py`): recursive walk honoring ignore lists.
  Skips minified bundles and TypeScript declaration files.
* **Read once**: file texts feed both index construction and workers.
* **Index** (`repo_index.py`): per-language symbol tables, per-file
  symbol/import/star/export maps, path sets, tsconfig alias zones.
  Rebuildable per file (`_index_one`/`forget_file`) for the LSP server.
* **Parse** (`parsers.py`): Python via stdlib `ast` + `tokenize`;
  JavaScript/TypeScript, Go, and C via constrained line scanners plus
  import extraction. Broken Python buffers degrade to comment-only
  facts instead of failing.
* **Check** (`checkers.py`): five checkers, each a pure function of
  file facts plus the index. A checker never crashes a scan.
* **Report** (`reporters.py`): terminal, JSON, SARIF 2.1.0, self-contained
  HTML. Sorted deterministically by path, line, checker.

## Duplication that exists on purpose

* JS import extraction appears twice: an alias map for symbol skipping
  (`_js_imports`) and structured entries for import verification
  (`_js_import_entries`). They answer different questions (is it external
  vs. does the target export it); merging them would couple the checkers.
* Python and JS import checkers share resolution helpers where the
  semantics coincide (scope rules) and diverge where the languages do
  (guarded imports, star exports, default exports).

## Extension points

* New checker: add a `check_*` function of `(FileFacts, RepoIndex)`,
  register it in `CHECKERS` with a description, add fixtures plus tests.
  No other file changes.
* New language: add a `parse_*` function returning `FileFacts`, a suffix
  entry, index extraction, and the reserved-word sets. Only with a
  measured real-world corpus showing acceptable precision.
* New output: add a function in `reporters.py` plus a `--format` choice.

## Invariants

* Checkers are pure: no I/O, no network, no subprocesses, no wall-clock
  reads. The only subprocess in the codebase is `git` for `--changed`.
* Findings are stable: same tree plus same config yields byte-identical
  JSON. Parallel and serial scans produce identical output (tested).
* Zero runtime dependencies: the standard library only. Test-only tools
  are fine; shipped code is not.
