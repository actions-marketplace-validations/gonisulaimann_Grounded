# Changelog

All notable changes to `grounded` are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added
- `grounded init-agent --skill` installs the agent skill to
  `~/.claude/skills/grounded/` (all projects); `--skill-project`
  installs to `.claude/skills/grounded/` (this repo only). Skill files
  ship inside the package, so pip/uvx/brew installs work with no repo
  checkout. Bare `init-agent` behavior is unchanged.
- ClawHub publish path documented (`docs/agent-skill.md`); skill
  frontmatter name is now `grounded` to match its directory.
- `stale-doc-ref` (experimental, opt-in): fenced Markdown code examples
  calling symbols defined nowhere in the repo. Excluded from every
  default set; run with `--enable stale-doc-ref`. Markdown files are
  collected only when it runs, so default scans are unchanged.
- VS Code / Cursor extension scaffold (`editors/vscode/`): thin LSP
  client with explicit server resolution (PATH, `serverPath`, or
  consent-gated uvx). Not yet published; install from VSIX.

## [0.12.1] - 2026-09-21

### Changed
- Documentation overhaul: language ribbon with 9 translated quickstarts
  (AR, ES, PT-BR, FR, DE, CN, JP, RU, KR), expanded rules reference,
  corrected installation pins.
- Action Marketplace description covers PR annotations, baselines, SARIF.

## [0.12.0] - 2026-09-21

### Added
- Claim graph: symbols, imports, and comment claims as queryable edges.
- `grounded impact SYMBOL`: definers, importers, and comment claims for
  a symbol (answers "what breaks if I rename this?").
- MCP `blast_radius` tool with the same query surface for agents.

## [0.11.1] - 2026-09-21

### Added
- Unknown suppression ids warn on stderr (`grounded-disable: stale-symobl`
  no longer fails silently); the gate exit code is unchanged.

### Changed
- `fix` help text covers symbol renames, not just file paths.

## [0.11.0] - 2026-09-21

### Added
- Alias refinements: node_modules mappings skipped, unresolvable aliases
  report as drift (generated files stay out of the default gate),
  `export type` recognized, bare `export *` marks export sets unknowable.
- Mid-typing tolerance: broken Python buffers still yield comment
  diagnostics; editor flicker test pins it.

### Changed
- Memory optimization declined after measurement: index heap 31 MB on
  2,977 files (~10 KB/file); interning moved nothing. Documented as the
  scaling ceiling instead (~500 MB at 50k files).

## [0.10.1] - 2026-09-21

### Fixed
- Config files work on Python 3.10 via a strict built-in TOML subset
  reader (parity-tested against tomllib; verified on real 3.10).

## [0.10.0] - 2026-09-21

### Added
- JS/TS path-alias resolution: tsconfig `paths` auto-detected per
  directory (JSONC tolerant, `extends` chains, longest-prefix wins) plus
  manual `path_aliases` config. Unresolvable aliases report as drift.
- Config files work on Python 3.10 via a strict built-in TOML subset
  reader (parity-tested against tomllib; anything outside reads as
  unreadable, never half-applied).

## [0.9.0] - 2026-09-21

### Added
- `stale-import` now covers JS/TS relative imports (module existence,
  named/default exports, star re-exports; bare and asset specifiers skip).
- LSP incremental index: buffer edits re-index the file, peer documents
  re-diagnose, close reverts to disk. Single-file patch measured 9-22 ms.
- `grounded init-agent`: generates Claude hook, Cursor rule, Aider config
  (idempotent, refuses invalid JSON, never merges YAML blindly).
- Parallel threshold retuned to 512 files / 8 workers on measurements
  (parallel loses below ~500 files; 12-way oversubscription regressed).
- Disk cache (`scan --cache`): mtime+size keyed per-file findings.

## [0.8.0] - 2026-09-21

### Added
- `stale-import` checker: resolvable Python imports verified (module
  exists; name defined, re-exported, or a submodule). Star re-exports,
  tuple targets, nested compat blocks, `__getattr__` modules, and dunder
  imports handled; guarded and external imports never flagged.
- `grounded lsp`: stdlib LSP 3.17 server (diagnostics + quickfix actions),
  protocol-tested including a full heal loop.
- File-scoped `scan`/`fix`: a file argument scopes reporting (and
  rewriting) to that file; the index still spans the tree.
- Agent recipes: verified Aider lint loop, Claude Code hooks JSON, Cursor
  rules snippet.
- `examples/bench/bench.py`: reproducible pre-test-filter timings.

### Changed
- General Markdown verification declined after measurement; symbol rename
  autofix requires scope plus similarity (ties never touch).

## [0.7.1] - 2026-09-21

### Changed
- Distribution renamed to `grounded-lint` on PyPI (bare `grounded` was
  claimed). Command (`grounded`), import package, and action behavior
  unchanged.

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
