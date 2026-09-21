# Benchmarks

All numbers below are reproducible and measured on physical hardware.
Nothing here is projected, estimated, or compared against unmeasured
runners.

## Reproducible harness

`examples/bench/bench.py` measures the pre-test-filter path (stdlib
only, pytest leg skipped when pytest is absent):

```console
python3 examples/bench/bench.py
```

```text
pre-test-filter benchmark (best of 7, wall clock)
  A. grounded in-process, single file :      0.6 ms
  B. grounded cold CLI, single file   :     58.0 ms
```

* **A** is the agent-loop path: parse plus check inside a running
  process (MCP server, LSP server, editor hook).
* **B** is dominated by Python interpreter startup, not by checking.
* No test-runner comparison is published: tests catch everything, and
  any head-to-head number without a fixed fixture would be marketing.

## Full-tree scans (measured 2026-09-21)

Machine: MacBook Air (Apple silicon, 10 cores), Python 3.14.6.
Harness: `python3 bench/run.py <repo> --reps 3` (one invocation per
repo so RSS is attributable; each rep is a fresh cold-CLI process,
median reported). Repos are shallow clones at 2026-09-21 main.

| Tree | Files | Median | Throughput | Peak RSS | Findings |
|---|---|---|---|---|---|
| django | 2,977 | 16.7 s | 178 files/s | 468 MB | 33 smells, 5 lies |
| cpython (3.13) | 3,078 | 47.2 s | 65 files/s | 1,251 MB | 1,221 smells, 68 lies, 7 drifts |
| grpc-go | 1,068 | 4.4 s | 242 files/s | 250 MB | 5 smells, 5 lies |
| axios | 246 | 1.0 s | 255 files/s | 39 MB | 1 lie |
| express | 141 | 0.6 s | 229 files/s | 33 MB | 0 |
| flask | 83 | 1.1 s | 75 files/s | 43 MB | 0 |

Small-tree times are dominated by interpreter startup (~58 ms, see
above), not by checking. Memory scales with total source held for the
single-pass index build. Parallelism engages automatically at 512+
files; repeat scans reuse per-file results with `scan --cache`.

## Corpora results (2026-09-21 run, all classified)

| Repository | Findings | Standing |
|---|---|---|
| django | 33 smells, 5 lies | 4 lies in deliberately-broken test fixtures (`broken_app`, `import_error`, `broken_tag`, staticfiles test data); **1 true lie**: `test_fallback.py` references `CookieTests.test_cookie_max_length()`, which exists nowhere |
| cpython | 1,221 smells, 68 lies, 7 drifts | smells are stdlib `XXX`/`TODO` markers; sampled lies are moved/removed files (**true**: `Include/code.h` → `Include/cpython/code.h`, `Tools/scripts/*` reorg, `Lib/distutils/msvccompiler.py` removal) and deliberately-broken `test_import` fixtures; residual lies are platform APIs (documented limitation) plus one cross-branch number comparison (known `number-drift` weakness) |
| grpc-go | 5 smells, 5 lies | **true**: `NewContextWithHandshakeInfo()` documented but defined nowhere; suspected-true `toLoadReport()`; FPs: inlined-copy provenance mention, one cross-file local |
| axios | 1 lie | known FP: import from a `tsc`-generated `declarations/` dir that exists only during tests |
| express | 0 | was 101 stale-import lies before the CJS-interop and `./`-normalization fixes (same run) |
| flask | 0 | — |

Earlier runs (different hardware): cobra (Go) 6 smells + 2 confirmed
rename-rot lies; redis (C) 63 smells + 13 lies (7 confirmed true).
Remaining false positives fall in documented classes (vendored code,
kernel idioms, platform APIs, prose verbs); see Limitations.

## What the benchmark round fixed

Measuring on real repos paid for itself four times over, all in
`stale-import` and `stale-symbol-ref`:

* PEP 695 `type X = ...` aliases are runtime bindings (was: 32 false
  lies on CPython's `_pyrepl`).
* `./`-prefixed index paths are normalized before export lookup (was:
  101 false lies on express examples).
* `require('./x').prop` is a named import, not a default import.
* Default imports from CJS-shaped targets are valid via Node interop
  and stay silent; the check applies to ESM targets only.
* `from . import X` follows star re-exports and yields to
  `globals().update()` namespaces.
* `Xxx`/`XXX` placeholder names stay silent in `stale-symbol-ref`.
