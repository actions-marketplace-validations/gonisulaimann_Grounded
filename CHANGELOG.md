# Changelog

All notable changes to `grounded` are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

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
