"""Repo-scale benchmark: throughput, latency, memory, findings (stdlib only).

Usage:
    python3 bench/run.py /path/to/repo [...] [--reps 3] [--enable a,b]

Each timed rep is a fresh cold-CLI process (interpreter startup included:
that is the real `grounded scan` cost). One untimed warm run first for
page cache. Peak RSS via resource.ru_maxrss (children). Findings from one
`--format json` run. Prints one JSON document to stdout.

Not a CI gate (shared runners are noisy); run on quiet hardware and
record machine + date alongside numbers.
"""

from __future__ import annotations

import json
import os
import platform
import resource
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"


def run_scan(root: str, extra: list[str]) -> tuple[int, float, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    cmd = [sys.executable, "-m", "grounded.cli", "scan", root, "--no-color"] + extra
    start = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return proc.returncode, time.perf_counter() - start, proc.stdout


def rss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    if sys.platform == "darwin":
        return rss / 1e6  # bytes on macOS
    return rss / 1e3  # kilobytes on Linux


def bench_repo(root: str, reps: int, extra: list[str]) -> dict:
    out_files = subprocess.run(
        [sys.executable, "-m", "grounded.cli", "list", root],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(SRC)},
    ).stdout.splitlines()
    n_files = sum(1 for line in out_files if line.strip())
    run_scan(root, extra)  # warm, untimed
    times = [run_scan(root, extra)[1] for _ in range(reps)]
    median = statistics.median(times)
    _, _, raw = run_scan(root, ["--format", "json"] + extra)
    peak = rss_mb()
    try:
        items = json.loads(raw) if raw.strip() else []
    except ValueError:
        items = []
    by_sev: dict[str, int] = {}
    by_checker: dict[str, int] = {}
    for f in items:
        by_sev[f.get("severity", "?")] = by_sev.get(f.get("severity", "?"), 0) + 1
        by_checker[f.get("checker", "?")] = by_checker.get(f.get("checker", "?"), 0) + 1
    return {
        "repo": Path(root).name,
        "files": n_files,
        "seconds_median": round(median, 3),
        "seconds_all": [round(t, 3) for t in times],
        "files_per_sec": round(n_files / median, 1) if median > 0 else 0,
        "rss_mb": round(peak, 1),
        "findings": {"total": len(items), "by_severity": by_sev, "by_checker": by_checker},
    }


def main() -> int:
    repos: list[str] = []
    reps = 3
    extra: list[str] = []
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        if argv[i] == "--reps" and i + 1 < len(argv):
            reps = int(argv[i + 1])
            i += 2
        elif argv[i] == "--enable" and i + 1 < len(argv):
            extra = ["--enable", argv[i + 1]]
            i += 2
        elif argv[i].startswith("--"):
            i += 1
        else:
            repos.append(argv[i])
            i += 1
    if not repos:
        print("usage: python3 bench/run.py <repo...> [--reps N] [--enable a,b]", file=sys.stderr)
        return 2
    doc = {
        "machine": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "reps": reps,
        "repos": [bench_repo(r, reps, extra) for r in repos],
    }
    print(json.dumps(doc, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
