# Installation

Grounded requires **Python 3.10+** and has **zero external runtime dependencies**.

---

## macOS & Linux (Homebrew)

The recommended installation method for macOS and Linux users:

```bash
brew install gonisulaimann/tap/grounded
```

Verify your installation:

```bash
grounded --version
```

---

## Python Package Managers

### With `pip`
```bash
pip install grounded-lint
```

### With `uv` (Fastest)
```bash
# Install as a global CLI tool:
uv tool install grounded-lint

# Or run directly without installing:
uvx grounded-lint scan .
```

### With `pipx`
```bash
pipx install grounded-lint
```

---

## From Source

```bash
git clone https://github.com/gonisulaimann/Grounded.git
cd Grounded
pip install -e .
```

---

## Pre-Commit Hook

Add Grounded as a pre-commit hook to catch broken references and imports on every commit:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/gonisulaimann/Grounded
    rev: v0.12.0
    hooks:
      - id: grounded
```

---

## GitHub Actions CI

Gate pull requests in CI using the official GitHub Marketplace Action:

```yaml
# .github/workflows/ci.yml
- uses: gonisulaimann/Grounded@v0.12.0
  with:
    changed-base: origin/main
    fail-on: lie
```
