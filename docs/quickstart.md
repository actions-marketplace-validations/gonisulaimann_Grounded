# Quickstart (5 minutes)

Install, run, read output, fix one finding, gate CI. Every command below
runs verbatim.

## 1. Install (pick one)

```console
pip install grounded-lint
```

```console
brew install gonisulaimann/tap/grounded
```

```console
uvx --from grounded-lint grounded --version
```

Verify:

```console
grounded --version
```

## 2. Scan something

```console
mkdir -p demo/pkg && cd demo
printf 'def get_account(uid):\n    return uid\n' > pkg/core.py
printf 'from .core import get_user_account\n' > pkg/views.py
touch pkg/__init__.py
grounded scan . --no-color
```

Expected output (paths and counts exactly as shown):

```text
LIE pkg/views.py:1 [stale-import] `get_user_account` imported from `pkg/core.py` but never defined there
    claim: from .core import get_user_account
    evidence: `pkg/core.py` exists but defines no `get_user_account`.
    fix: Check for a rename in `pkg/core.py` (or a moved submodule).

grounded: 1 finding(s) in 3 file(s), 1 lie(s), 0 drift(s), 0 smell(s).
```

Exit code is `1` because the default gate (`--fail-on lie`) tripped.

## 3. Ask what touches the name, then fix

```console
grounded impact get_user_account .
grounded fix . --dry-run
```

The impact query lists definers (none), importers, and comment claims.
`fix` rewrites only unambiguous matches and prints what it would change;
drop `--dry-run` to apply.

## 4. Gate CI

```console
grounded baseline . --output .grounded-baseline.json
git add .grounded-baseline.json
grounded scan . --baseline .grounded-baseline.json
```

From here, only new findings fail the build. Next: [Rules](rules.md),
[Agents](agents.md), [Benchmarks](benchmarks.md).
