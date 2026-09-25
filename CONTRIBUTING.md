# Contributing to Grounded

This document is for people wanting to contribute to **Grounded** (`grounded-lint`).
Contributions are proposed using [GitHub](https://github.com) [pull requests](https://docs.github.com/pull-requests) to the [Grounded repository](https://github.com/gonisulaimann/Grounded).

This document assumes that you already know how to use GitHub and Git.
If that's not the case, we recommend learning about it [here](https://docs.github.com/en/get-started/quickstart/hello-world).

---

## Overview
[overview]: #overview

This file contains general contributing guidelines, architectural invariants, and verification standards.
More specific information about individual parts of Grounded can be found here:

- [`README.md`](./README.md): High-level architecture, checker taxonomy, and CLI usage.
- [`docs/`](./docs/): Deep dives on checkers, suppression mechanics, and editor integrations.
- [`corpus/`](./corpus/README.md): The multi-language benchmark cases and verification harness.
- [`agent-skill/`](./agent-skill/): The standalone skill files installed by `grounded init-agent`.
- [`SECURITY.md`](./SECURITY.md): Security policy and vulnerability disclosure procedures.

### Table of Contents

- [Overview][overview]
- [Project Boundaries & Invariants](#project-boundaries--invariants)
  - [What Fits](#what-fits)
  - [What Does Not Fit](#what-does-not-fit)
- [How to Propose Changes](#how-to-propose-changes)
  - [Reporting an Issue](#reporting-an-issue)
  - [Submitting a Pull Request](#submitting-a-pull-request)
- [The Verification Triad](#the-verification-triad)
- [Practical Contributing Advice](#practical-contributing-advice)
  - [The Committer's Perspective](#the-committers-perspective)
  - [How to Reduce Review Cycles](#how-to-reduce-review-cycles)
  - [Pinging Etiquette](#pinging-etiquette)
- [Commit Message & AI Attribution Policy](#commit-message--ai-attribution-policy)
- [Development Setup](#development-setup)

---

## Project Boundaries & Invariants
[project-boundaries--invariants]: #project-boundaries--invariants

Grounded is deliberately constrained. Its value lies in high signal: **zero false positives**, **zero external runtime dependencies**, and **positive proof of contradiction**.

### What Fits

- **Provable false positive fixes**: Code changes where a checker flagged a contradiction that is actually true, accompanied by a minimal reproducing fixture.
- **Missed detections (false negatives)**: New or refined detection patterns for contradictions within the supported checkers, accompanied by a fixture.
- **Language support within existing architecture**: Extractors for new languages that conform to the two-pass snapshot architecture (imports, exports, identifiers, and comments) without external parsers.
- **Documentation and fixtures**: Improvements to docs, regression fixtures, benchmark cases, and editor/agent integrations.

### What Does Not Fit

- **External runtime dependencies**: Grounded runs strictly on the Python Standard Library (`ast`, `re`, `pathlib`, `difflib`, `json`, `concurrent.futures`). Do not add external packages to `dependencies` in `pyproject.toml`.
- **Stylistic, format, or docstring-contract linting**: Grounded does not check docstring styles (params, returns, raises). Use [darglint](https://github.com/terrencepreilly/darglint), [pydoclint](https://github.com/jsh9/pydoclint), or [eslint-plugin-jsdoc](https://github.com/gajus/eslint-plugin-jsdoc).
- **Dead/commented-out code detection**: Use [Ruff ERA001](https://docs.astral.sh/ruff/rules/commented-out-code/) or [eslint-plugin-comment-cleaner](https://github.com/es-tooling/eslint-plugin-comment-cleaner).
- **Network calls, LLM calls, or heavy runtime tracing**: Grounded runs entirely offline and deterministically on local source trees.
- **Silent failure on unhandled exceptions**: Clean code should be silent, but syntax or checker crashes must fail loudly (`exit 3`) rather than quietly swallow exceptions.

---

## How to Propose Changes
[how-to-propose-changes]: #how-to-propose-changes

### Reporting an Issue

Before filing an issue, ensure your finding meets the project definition of a contradiction. Please include all three items, or the issue cannot be acted on:

1. **A minimal reproducible fixture**: A few lines of code showing the comment/reference and the surrounding definitions.
2. **Current vs. Expected output**: Paste the exact terminal output from `grounded scan` alongside what you expected.
3. **Checker ID**: Include the rule ID in the issue title, e.g. `[stale-symbol-ref] False positive on dynamic getattr`.

### Submitting a Pull Request

1. **Fork and branch**: Create a descriptive feature branch from `main` (e.g. `fix/stale-import-relative-root` or `feat/go-type-alias-support`).
2. **Implement changes**: Keep changes minimal, focused, and idiomatic Python (Python 3.10+ standard library).
3. **Fulfill the Verification Triad**: Every functional change must pass unit tests, include a corpus fixture, and satisfy `corpus/run.py`.
4. **Self-scan gate**: Run `grounded scan src` (or `PYTHONPATH=src python3 -m grounded.cli scan src`) to guarantee the core source tree remains clean of findings.
5. **Update CHANGELOG**: Add a concise entry under the `## [Unreleased]` section of [`CHANGELOG.md`](./CHANGELOG.md).

---

## The Verification Triad
[the-verification-triad]: #the-verification-triad

Grounded enforces a three-pillar verification standard for any checker modification or bug fix:

```
                  ┌───────────────────────────────┐
                  │ 1. Unit Tests                 │
                  │ python3 -m unittest           │
                  │ Granular parser/AST logic     │
                  └───────────────┬───────────────┘
                                  │
                                  ▼
┌───────────────────────────────┐   ┌───────────────────────────────┐
│ 2. Corpus Case                │   │ 3. Benchmark Verification     │
│ corpus/cases/<case-id>/       │──▶│ python3 corpus/run.py         │
│ Isolated fixtures + expected  │   │ 1.00 precision & 1.00 recall  │
└───────────────────────────────┘   └───────────────────────────────┘
```

1. **Unit Tests**:
   - Located in [`tests/`](./tests/).
   - Verifies AST edge cases, suppression flags, regexes, and CLI options.
   - Run: `python3 -m unittest discover -s tests`
2. **Corpus Case**:
   - Located in `corpus/cases/<case-id>/`.
   - Contains a minimal isolated repo tree reproducing the scenario, along with `expected.json` declaring the exact expected findings.
3. **Corpus Verification**:
   - Run: `python3 corpus/run.py`
   - **Must report 100% pass**: precision `1.00` and recall `1.00` across all registered corpus cases. Regressions are not accepted.

4. **Real-repo precision gate** (anything touching `src/`):
   - Run: `python3 bench/precision.py` (clones 25 pinned repos once into `.precision-repos/`).
   - Every lie/drift must already be labeled in `bench/precision/ledger.json`. A new finding needs a verdict (`TP`/`FP`) and a one-line reason; a new FP also needs an entry in `known_fp` and should come with a fix or a stated reason there is none.

### Adding or changing a rule

1. **Start with the corpus case**, not the code: `corpus/cases/<checker>-<behavior>/` with the smallest tree that shows the behavior. Every silence case needs a **positive control**, a planted real rot in the same case that must still fire, or the case can pass vacuously.
2. **Put the code in its family module** under `src/grounded/checkers/` (or a new module for a new family), and register a new checker in `checkers/__init__.py`: `CHECKERS`, `CHECKER_DESCRIPTIONS`, and `OPT_IN_CHECKERS` (new checkers always start opt-in).
3. **Suppressions only ever silence.** Write each one as a named, commented rule that cites where the false positive was seen (`seen: django's ...`). Never "fix" a fixture to make a case pass.
4. **Measure on real repos:** `bench/precision.py` for default checkers, and `bench/recall.py` to prove planted rot still fires inside real trees. Graduating an opt-in checker to default needs a recorded real-repo precision round (see `docs/rules.md`).
5. **Tests** go in the topic file under `tests/` (`test_imports.py`, `test_docs.py`, ...).
6. **Watch `.gitignore`.** Fixture directories named `build/` or `dist/` are ignored by this repo's `.gitignore`: `git add -f` them, and check the case passes from a clean clone.

---

## Practical Contributing Advice
[practical-contributing-advice]: #practical-contributing-advice

### The Committer's Perspective

Maintainers and review volunteers review pull requests in their free time. A review consists of checking:

- Does this introduce any external runtime dependencies?
- Can this create false positives on valid user code?
- Does it preserve the exit code contract (`0` = clean/acceptable, `1` = findings meeting `--fail-on`, `2` = invocation error, `3` = unhandled checker crash)?
- Are edge cases (relative imports, star imports, comments inside multiline strings) handled correctly?

### How to Reduce Review Cycles

- **Isolate the change**: One bug fix or one feature per pull request. Do not combine architectural changes with formatting cleanups.
- **Show the before/after**: In your PR description, include terminal snippets showing the exact finding before your fix and the output after.
- **Cite the language spec**: If the change touches language-specific mechanics (e.g. Python PEP 562 module `__getattr__`, Go build tags, TypeScript path mappings), link the relevant official specification in the PR description.

### Pinging Etiquette

If your pull request has had no activity for 7 days, feel free to leave a polite comment asking for review. Avoid pinging individual maintainers repeatedly on social channels or unrelated threads.

---

## Commit Message & AI Attribution Policy
[commit-message--ai-attribution-policy]: #commit-message--ai-attribution-policy

### Commit Messages

Use clear, imperative commit messages following the Conventional Commits style:

```
fix(stale-import): resolve relative dot-imports in package roots

When importing from parent packages using relative dot syntax, the
resolver failed to account for implicit namespace packages. This adds
directory traversal resolution before reporting an import as stale.

Fixes #42
```

### AI-Assisted Contributions

AI coding assistants (Claude, ChatGPT, Copilot, Antigravity, etc.) are welcome tools, but the human contributor remains strictly accountable for the submitted code.

- **Mandatory verification**: Do not submit generated code without running both `python3 -m unittest discover -s tests` and `python3 corpus/run.py` locally.
- **Attribution**: If an AI assistant authored or significantly shaped the patch, document it using git commit trailers:
  ```
  Assisted-by: Claude Code <anthropic/claude-3.7-sonnet>
  ```
  *(Reserve `Co-authored-by:` for human collaborators who hold copyright).*
- **Explanation**: You must be able to explain the rationale of every line in your PR when reviewers ask questions.

---

## Development Setup
[development-setup]: #development-setup

Grounded requires **Python 3.10** or newer.

```console
# Clone the repository
git clone https://github.com/gonisulaimann/Grounded.git
cd Grounded

# Install in editable mode
pip install -e .

# Run unit tests
python3 -m unittest discover -s tests

# Run the corpus benchmark harness
python3 corpus/run.py

# Verify the self-scan gate
PYTHONPATH=src python3 -m grounded.cli scan src

# Test the demo fixture tree (expected: 10 findings)
grounded scan examples/v2demo
```
