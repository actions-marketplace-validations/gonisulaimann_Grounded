# FAQ

## Will grounded slow down my editor or agent loop?

Single-file checks measure 0.6 ms in-process and about 58 ms cold CLI
(Python startup dominates). Run `python3 examples/bench/bench.py` to
reproduce both numbers on your machine.

## Why did my finding disappear after an edit?

Baselines fingerprint checker, path, and claim text, never line numbers.
Editing the offending line changes the claim, so it correctly re-fires
as new. Unrelated edits elsewhere never churn entries.

## Why is a real problem not reported?

Probably one of these, all deliberate:

* The name is imported, a builtin, a keyword, or used in the same file.
* The reference sits on a Sphinx/JSDoc contract line (another linter's
  surface), in an illustrative example, or next to a ticket.
* The import is guarded (`try/except`, `TYPE_CHECKING`, version checks),
  external, or a star import.
* The mention has no backticks and no reference verb. Bare prose is not
  checked: the false-positive cost exceeds the recall gain.

File an issue with a minimal fixture if you believe a case is wrong;
that is how the rules improve.

## Why did grounded flag something that looks fine?

Open an issue with the fixture and the output. Known residual classes
(vendored code, kernel idioms, platform APIs, paper algorithms) are
listed under Limitations in the README. A reproducible false positive
is a bug report, not a complaint.

## Does grounded send my code anywhere?

No. No network calls exist in the shipped code. MCP and LSP transports
are local stdio. The only subprocess is `git` for `--changed`.

## Does it work on Windows?

The test matrix runs Ubuntu CI across Python 3.10–3.14. Path handling
uses `pathlib` throughout and avoids POSIX-only assumptions, but Windows
runs are not in CI. Reports welcome.

## Why no plugin system?

Five checkers with measured precision beat fifty speculative ones. New
checkers require a fixture, tests, and a real-world corpus measurement.
See Contributing in the README.
