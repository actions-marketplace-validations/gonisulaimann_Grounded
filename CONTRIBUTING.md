# Contributing to grounded

Thanks for looking here. This project stays small on purpose: four checkers,
stdlib only, no network. Contributions that keep it that way are welcome.

## What fits

- Fixes where a finding is provably wrong (false positive with a minimal
  fixture proving it).
- Missed detections in the four covered checkers, with a fixture.
- Language support that reuses the existing architecture (imports plus
  identifiers, snapshot only).
- Docs, fixtures, and tests.

## What does not fit

- New runtime dependencies. Stdlib only is a project rule.
- Docstring contract checks (params, returns, raises). Use
  [darglint](https://github.com/terrencepreilly/darglint),
  [pydoclint](https://github.com/jsh9/pydoclint), or
  [eslint-plugin-jsdoc](https://github.com/gajus/eslint-plugin-jsdoc).
- Commented-out code detection. Use
  [Ruff ERA001](https://docs.astral.sh/ruff/rules/commented-out-code/).
- Network calls, LLM calls, or git-history analysis in the scan path.

## Issue format

Please include all three, or the issue cannot be acted on:

1. A minimal fixture (a few lines showing the comment and the code).
2. Current output vs expected output (paste the terminal lines).
3. The checker id in the issue title, e.g. `[stale-symbol-ref] ...`.

## Pull request checklist

- [ ] Fixture added under `examples/` or `tests/` reproducing the case.
- [ ] Tests added (`python -m unittest discover -s tests`, all green).
- [ ] `grounded scan src` reports clean (self-scan gate).
- [ ] No new runtime dependencies.
- [ ] CHANGELOG.md entry under Unreleased.

## Development setup

```console
git clone https://github.com/gonisulaimann/Grounded.git
cd Grounded
pip install -e .
python -m unittest discover -s tests
grounded scan examples/v2demo   # fixture tree, expect 10 findings
```

## Releases

Maintainers cut releases with a tag (`vX.Y.Z`), release notes, and the
CHANGELOG entry. The publish workflow handles PyPI; the release form's
Marketplace checkbox handles the GitHub Marketplace listing.
