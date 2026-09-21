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

## Full-tree scans (measured)

| Tree | Files | Cold scan |
|---|---|---|
| requests | 37 | < 0.5 s |
| axios | 248 | < 1 s |
| django | 2,977 | ~5 s parallel, ~10 s serial |

Parallelism engages automatically at 512+ files (measured crossover;
below that, process spawn costs more than it saves). Repeat scans reuse
per-file results with `scan --cache` (roughly 2x on large trees; the
index still rebuilds from disk).

## Corpora results

| Repository | Findings | Standing |
|---|---|---|
| requests | 4 smells | genuine untracked markers |
| axios | 1 lie | intentionally-broken test fixture |
| django | 33 smells, 4 fixture lies, 1 true lie | the lie is a confirmed rename-rot |
| cobra (Go) | 6 smells, 2 lies | both lies confirmed rename-rot |
| redis (C) | 63 smells, 13 lies | 7 confirmed true, rest in documented limitation classes |

Remaining false positives fall in documented classes (vendored code,
kernel idioms, platform APIs, prose verbs); see Limitations.
