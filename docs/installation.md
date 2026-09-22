# Installation

Requires Python 3.10 or later. Zero runtime dependencies in every method.

## Homebrew (macOS and Linux)

```console
brew install gonisulaimann/tap/grounded
grounded --version
```

## pip

```console
pip install grounded-lint
grounded --version
```

## uvx (no Python management)

Runs without installing anything persistently:

```console
uvx --from grounded-lint grounded scan .
```

## From source

```console
git clone https://github.com/gonisulaimann/Grounded.git
cd Grounded
pip install -e .
python -m unittest discover -s tests
```

## Verify

```console
grounded scan --help
grounded explain stale-symbol-ref
```

## Pre-commit hook

```yaml
repos:
  - repo: https://github.com/gonisulaimann/Grounded
    rev: v0.15.0
    hooks:
      - id: grounded
```
