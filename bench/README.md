# Repo-scale benchmark harness

`run.py` measures cold-CLI wall time (median of N fresh processes),
throughput, peak RSS, and findings per repo. Stdlib only.

```console
python3 bench/run.py /path/to/repo [...] [--reps 3] [--enable a,b]
```

Prints one JSON document. Methodology notes:

* Each timed rep is a fresh process: interpreter startup is included,
  because that is the real `scan` cost. One untimed warm run first.
* Peak RSS comes from `resource.ru_maxrss` over waited-for children and
  is a session maximum, so run **one repo per invocation** when RSS
  attribution matters.
* Findings come from a separate `--format json` run in the same process.
* Not a CI gate: shared runners are too noisy for timing. The numbers
  in `docs/benchmarks.md` were produced on quiet hardware with machine
  and date recorded; reproduce them by cloning the same repos
  (`--depth 1` at the recorded date is close enough) and re-running.

Repo choice is deliberate, not flattering: large Python (django,
CPython), large Go (grpc-go), mid TypeScript (axios), small JS/Python
anchors (express, flask). `vuejs/core` was attempted twice and failed
to clone on the author's network; pull requests adding repos (with
numbers) are welcome, provided findings are classified, not just
counted.
