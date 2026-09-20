# Changelog

All notable changes to `grounded` are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.7.0] - 2026-09-21

### Added
- `grounded mcp`: stdlib MCP server over stdio (initialize negotiation,
  tools/list, tools/call, ping) with `check_path` and `explain_checker`
  tools; paths confined to the server root. Verified with an independent
  Node client against the spec.
- Symbol renames in `grounded fix`: exactly one similar (ratio 0.75+),
  same-directory candidate, or nothing is touched. Proven on the case
  that killed naive similarity autofix.
- Per-symbol defining files in the repo index (powers scope proximity).

### Changed
- General Markdown verification declined after measurement (199 import
  claims trivially valid, flag claims mostly external tools on axios
  docs); overlaps readme-ci, doccident, doc-drift, and docverity.

## [0.6.0] - 2026-09-17

### Added
- C support (`.c`/`.h`): functions incl. split declarations and K&R style,
  types, macros, function-pointer members, `#include` maps.
- Parallel scanning (`--jobs N`, auto by file count; identical output).
- VS Code task snippet using the bundled problem matcher.

### Changed
- JS index covers default exports, `module.exports` members, TS
  interfaces/types (no benchmark delta).

## [0.5.0] - 2026-09-17

### Added
- Go support: functions, types, and imports across all four checkers.
- `grounded fix [--dry-run]`: rewrites unambiguous stale file paths
  (unique same-basename match, comment lines only).
- Inline suppressions: `# grounded-disable: <id>` (`//` form for JS/TS/Go).
- First-party GitHub Action with problem matchers (verified live on a
  test PR); pre-commit hook definition.

### Changed
- File references use exact-path semantics and list same-named candidates;
  ticket-anchored history notes stay silent.
- Markdown files are not scanned: measured zero file-reference hits across
  160 documentation files, so no evidence of value (rejected).
- Symbol rename autofix rejected: string-similarity ranking picked the
  wrong target on a real case; suggestions stay advisory-only.

## [0.4.1] - 2026-09-17

### Fixed
- CI demo-fixture count to match current fixtures. No product changes.

## [0.4.0] - 2026-09-17

### Added
- Inline suppressions: `# grounded-disable: <id>` (`//` form for JS/TS).
- First-party GitHub Action (`action.yml`) with problem matchers for inline
  PR annotations; verified live on a test PR.
- Pre-commit hook definition (`.pre-commit-hooks.yaml`).

### Changed
- Commented-code suppression gate tightened (prose with a few keywords no
  longer qualifies).

## [0.3.0] - 2026-09-17

### Added
- `grounded baseline`: record current findings to `.grounded-baseline.json`.
  `scan --baseline FILE` then reports only new findings. Fingerprints hash
  checker, path, and claim text (never line numbers), so unrelated edits
  that shift lines do not churn the file. `--show-baselined` lists
  suppressed findings.
- `scan --changed [BASE]` (default: `HEAD`): report only findings on lines
  changed relative to BASE, for PR gates and pre-commit use. The tree is
  still fully scanned; reporting is filtered. Fails loudly outside git.
- `.gitignore`, contributor fixture guidelines in README.

## [0.2.0] - 2026-09-17

### Changed
- Deleted `param-mismatch`, `raises-mismatch`, `return-mismatch`,
  `commented-code`: verified redundant with darglint/pydoclint,
  eslint-plugin-jsdoc, and Ruff ERA001. `explain <id>` now routes to them.
- Rebuilt `stale-symbol-ref`: import-aware (stdlib/deps/relative), same-file
  scope proxy, Sphinx/JSDoc-tag scrubbing, dunder-typo class, verb-gated
  bare calls, acronym/negation/ticket gates, `difflib` rename suggestions.
- Hardened `stale-file-ref`: repo-scope rule, alphabetic extension list,
  placeholder + illustrative-example handling.
- Tightened commented-code suppression gate (suppression-only use).
- Excluded TypeScript declaration files (`.d.ts`/`.d.cts`/`.d.mts`).
- Measured on requests/axios/django: 844 → 42 findings; the 1 remaining
  lie is a confirmed true rename-rot.

### Added
- `examples/v2demo`: 4-file fixture tree (8 intended findings, designed
  silences for every suppression rule).
- MIT `LICENSE`, CI workflow (tests + self-scan gate), this file.

## [0.1.0] - 2026-09-16

Initial release: 8 checkers. Superseded by 0.2.0, which narrowed scope to
reference checks and removed contract checks covered by other linters.
