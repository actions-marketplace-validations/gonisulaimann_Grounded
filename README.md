# grounded

[![CI](https://github.com/gonisulaimann/Grounded/actions/workflows/ci.yml/badge.svg)](https://github.com/gonisulaimann/Grounded/actions/workflows/ci.yml)
[![PyPI version](https://badge.fury.io/py/grounded.svg)](https://pypi.org/project/grounded/)
[![Python](https://img.shields.io/badge/python-%3E%3D3.10-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Find dangling references in code comments. If a comment names a function
that no longer exists, or a file that is not there, `grounded` reports it
with the claim, the evidence, and a suggested fix.

```console
$ grounded scan ./src
LIE src/app.py:9 [stale-symbol-ref] Comment references `ghost_service` which is not defined here
    claim: `ghost_service()`
    evidence: `ghost_service` is not defined, imported, or used in this file,
              and no definition was found in 84 indexed source files.
    fix: Update the comment to the current name, or remove the reference.
```

Zero dependencies. No network access. Works on Python and JavaScript/TypeScript.

## Install

Requires Python 3.10 or later.

```console
pip install grounded
```

From source:

```console
git clone https://github.com/gonisulaimann/Grounded.git
cd Grounded
pip install -e .
```

## Usage

```console
grounded scan [PATH] [--format terminal|json|sarif|html] [--output FILE]
              [--fail-on lie|drift|smell|never]
              [--enable CHECKER,...] [--disable CHECKER,...]
              [--config grounded.toml] [--no-color] [--quiet]
grounded list [PATH]       # show files that would be scanned
grounded explain CHECKER   # describe a checker (including removed ones)
grounded init [--force]    # write a starter grounded.toml
```

`grounded scan` exits with status `1` when any finding meets `--fail-on`
(default: `lie`), `0` otherwise. Point it at CI and gate on the default.

Example output formats for tooling: `--format json` for scripts,
`--format sarif` for GitHub code scanning, `--format html` for a
self-contained report page (no external assets, works opened from disk).

## Rules

| ID | Default severity | What it reports |
|---|---|---|
| `stale-symbol-ref` | lie (error) | A comment names a call that resolves nowhere: not defined in the repo, not imported in the file, not used in the file, not a builtin or keyword. Prints rename suggestions when a close match exists. |
| `stale-file-ref` | lie (error) | A comment claims a path inside the repo tree that does not exist. References to other projects, frameworks, template namespaces, and placeholder paths are ignored. |
| `number-drift` | drift (warning) | A comment states a magic number (timeout, port, limit, threshold) that disagrees with adjacent code. |
| `fragile-anchor` | smell (note) | `line 42` anchors, `see above` / `see below` without a symbol, and workaround markers (`HACK`, `XXX`, `workaround`) with no ticket or expiry condition. |

A rule stays silent unless the contradiction is mechanical. Imported names,
standard library names, parameters, locals, attributes, docstring field
lists (`:param:`, `@param`), and illustrative examples ("For example …")
never produce findings.

## Configuration

`grounded init` writes a starter file. Settings also load from
`pyproject.toml` under `[tool.grounded]`.

```toml
# grounded.toml
disable = ["fragile-anchor"]
fail_on = "lie"
ignore_dirs = ["docs", "sandbox"]
ignore_files = ["generated.py"]
```

## Non-goals

Docstring contracts (parameter lists, return sections, raised exceptions)
are covered precisely by [darglint](https://github.com/terrencepreilly/darglint)
and [pydoclint](https://github.com/jsh9/pydoclint) for Python and
[eslint-plugin-jsdoc](https://github.com/gajus/eslint-plugin-jsdoc) for
JavaScript/TypeScript. Commented-out code is covered by
[Ruff ERA001](https://docs.astral.sh/ruff/rules/commented-out-code/) and
equivalent ESLint rules. `grounded` intentionally does not duplicate them;
`grounded explain <id>` points at the right tool for each removed check.

## Limitations

- Unformatted, unverbed name mentions are skipped. A rename noted without
  backticks or a reference verb ("calls", "see", "uses") will be missed.
  This trades recall for precision.
- Names imported from anywhere are treated as known elsewhere, including
  cross-module renames.
- Framework namespaces (template paths, URL names) are out of scope; such
  references stay silent instead of guessed.
- JavaScript/TypeScript analysis is syntactic (imports plus identifiers),
  not a full type graph.
- Rename suggestions use string similarity only; the first guess can miss.

## Development

```console
python -m unittest discover -s tests   # 42 tests, stdlib only, no extras
grounded scan src                      # self-scan gate, must report clean
grounded scan examples/v2demo          # fixture tree, expect 8 findings
```

## Contributing

Issues and pull requests are welcome. Please include:

- a minimal fixture (a few lines showing the comment and the code),
- current output vs expected output,
- the checker id in the issue title.

New checkers are accepted only with a fixture, tests, and no new runtime
dependencies (stdlib only is a project rule).

## License

MIT. See [LICENSE](LICENSE).
