# grounded — dangling-reference detector for code comments (v2)

**Finds references in comments that resolve nowhere — import-aware,
scope-aware, and measured against real repositories.**

```console
$ grounded scan ./src
LIE src/app.py:9 [stale-symbol-ref] Comment references `ghost_service` which is not defined here
    claim: `ghost_service()`
    evidence: `ghost_service` is not defined, imported, or used in this file,
              and no definition was found in 84 indexed source files.
    fix: Update the comment to the current name, or remove the reference.
```

Zero dependencies. No network. No history. No LLM. Pure stdlib Python.

> **History:** v1 ("epistemic linter", 8 checkers) was attacked by its own
> author against requests/axios/django and falsified — 5/8 checkers were
> redundant with superior incumbents and wild lie-precision measured ≈1–3%.
> v2 deletes those checkers, rebuilds the survivors with import/scope
> awareness, and re-measures: **844 → 42 findings, lie precision ≈1–3% →
> 1/1 confirmed plus zero-noise silences where 86 false lies stood.**
> Full evidence: [`ATTACK.md`](ATTACK.md). Small-n honesty applies: the
> headline rate rests on few remaining lies because v2 mostly stays quiet —
> which is the point. Quiet + right beats loud + wrong.

## Scope (deliberately narrow)

v2 checks 4 claim types with no exact incumbent:

| ID | Severity | Checks |
|---|---|---|
| `stale-symbol-ref` | lie | Comment names a call (`foo()` or `` `foo()` ``) that is not defined, imported, or used in-file — with rename suggestions |
| `stale-file-ref` | lie | Comment claims a path inside this repo's tree that does not exist (namespace/placeholder/illustrative-aware) |
| `number-drift` | drift | Magic number in a comment disagrees with adjacent code (±15 lines) |
| `fragile-anchor` | smell | `line 42` anchors, see-above/below, untracked HACK/XXX/workarounds |

Removed in v2 (verified redundant — `explain <id>` says where to go):

| Removed | Use instead |
|---|---|
| `param-mismatch` | darglint / pydoclint (py), eslint-plugin-jsdoc check-param-names + require-param (js) |
| `raises-mismatch` | darglint DAR402 / pydoclint DOC502–503 (py), eslint-plugin-jsdoc require-throws (js) |
| `return-mismatch` | darglint DAR201–202 / pydoclint DOC201–203 (py), eslint-plugin-jsdoc require-returns-check (js) |
| `commented-code` | Ruff ERA001 (py), eslint-plugin-comment-cleaner (js) |

Each removal was verified by executing the incumbent head-to-head
(darglint/pydoclint/Ruff/eslint-plugin-jsdoc all reproduce v1's true
positives with better precision plus types, autofix, and config).

## Why v2 is quiet (suppression rules, all evidence-driven)

`stale-symbol-ref` stays silent when the root is: defined anywhere in the
repo (incl. module-level assignments like `gettext_lazy = lazy(...)`),
imported in the claiming file (stdlib, deps, or relative), a stdlib module
or stdlib-class form (`tarfile`/`Tarfile`), used anywhere in the file's own
code (params, locals, attributes), a builtin/keyword, on a Sphinx-field or
JSDoc-tag line (incumbents' surface), a non-call backticked word that is not
a dunder (fields/options/prose), a bare call without reference-verb context,
an acronym-led bare call (`VALUES()`, `JSON_TYPE()` — SQL/system APIs), or
in a negated context ("Don't use RANDOM()", "not supported"). Bare claims
anchored to a ticket are left alone (deliberate external pointers).

`stale-file-ref` only judges claims about this repo's tree (explicitly
relative or first segment matching the repo root); skips placeholders
(`myapp`, `app_label`, …), illustrative examples ("For example …"), and
uses an alphabetic extension list (`HTTP/1.1` is not a file).

## Measured results (adversarial benchmark, ATTACK.md)

| Repo | v1 findings | v2 findings | v2 lies | Verdict on lies |
|---|---|---|---|---|
| psf/requests (37 files) | 46 (39 lie) | 4 (0 lie) | 0 | 25/26 symbol FPs eliminated; 4 smells are genuine untracked XXXs |
| axios/axios (246 files) | 86 (63 lie) | 0 | 0 | 29 return-FPs gone with the checker; 17 prose-as-code gone; options/props silent for the right reasons |
| django/django (2,977 files) | 712 (438 lie) | 38 (1 lie) | 1 | the 1 lie is a **confirmed true rename-rot** (`CookieTests.test_cookie_max_length()` vs real `test_max_cookie_length`) with a suggestion attached |

v2 demo: `grounded scan examples/v2demo` → 8 findings (4 lie, 2 drift,
2 smell), every one intended, every intended silence silent
(imported names, params/locals, Sphinx fields, external modules,
existing files).

## Install & run

Requires Python 3.10+.

```console
pip install grounded
grounded scan .
grounded scan ./src --format html --output report.html
grounded scan . --fail-on drift
grounded explain stale-symbol-ref
grounded explain param-mismatch   # points to the incumbent
grounded init
```

From source:

```console
pip install -e .
python -m unittest discover -s tests   # 42 tests, stdlib only
```

Exit code `1` when any finding meets `--fail-on` (default `lie`).

## Configuration

`grounded.toml` (or `pyproject.toml` `[tool.grounded]`):

```toml
disable = ["fragile-anchor"]
fail_on = "lie"
ignore_dirs = ["docs"]
```

## Architecture (v2 deltas)

- `parsers.py`: per-file **import maps** (AST for Python incl. relative
  forms; import/require regexes for JS/TS with bare-vs-relative
  classification) attached to `FileFacts`.
- `repo_index.py`: symbols now include **module-level assignments**
  (function-local names deliberately excluded); **`top_names`** (repo-root
  entries) power the repo-scope rule; `.d.ts`/`.d.cts` excluded at scan.
- `checkers.py`: 4 checkers + suppression machinery + `difflib`
  rename suggestions + `REMOVED_CHECKERS` pointers. Deleted ~300 lines of
  redundant checkers.
- Outputs unchanged: terminal / JSON / SARIF 2.1.0 / self-contained HTML.

## Limitations (honest residue)

- Bare-call channel is intentionally weak (verb + gates); rename-refs
  written without verbs or backticks are missed (recall cost of precision).
- Illustrative-example detection is proximity-based; exotic phrasing slips
  through either way.
- Suggestions are string-similarity only (`difflib`, cutoff 0.8); first
  guess can be wrong (django case suggested a cousin before the true
  rename — both listed when close).
- Cross-module renames where the name is imported are conservatively
  skipped (imported = "known elsewhere").
- Framework namespaces (Django templates, URLconfs) are out of scope by
  design — the repo-scope rule stays quiet instead of guessing.
- JS/TS analysis remains syntactic (imports + identifiers), not a full
  type graph. A `tsc`-powered edition would be the next precision step
  and would cost the zero-dependency property — deliberately not taken.
- Benchmark small-n warning: v2's lie precision rests on 1–2 remaining
  lies. The claim is "quiet and right on 3,200 files", not a percentage
  proven at scale.

## What was learned (attack → rebuild)

- The valuable unsolved residue was never "verify all beliefs" — it is
  **rename-aware dangling-reference detection**, and it only works with
  import graphs + scope proxies + framework humility.
- Every surviving v2 rule maps to a measured FP class; every deleted
  checker maps to an executed incumbent. Nothing in v2 is defended by
  taste.
- The zero-dependency property survived (still stdlib-only) but is no
  longer load-bearing for precision — import/scope reasoning did the work.
  If precision stalls again, spending the dependency budget on a real JS
  parser is the documented next step.
