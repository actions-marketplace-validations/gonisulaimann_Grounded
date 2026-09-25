"""`--changed` latency on real edits: replay a repository's recent commits
as uncommitted work and time `grounded scan --changed` on each.

    python3 bench/changed.py /path/to/clone [--commits 30] [--no-index-cache]

For each non-merge commit C (newest first) the harness checks out C's
parent, runs one untimed `--changed` scan (the previous hook run, which
leaves the index cache current), applies C's diff to the worktree, and
times one `--changed` scan in a fresh process, interpreter startup
included, because that is what a pre-commit hook or agent hook pays.
Prints one JSON document with every sample and p50/p95/max.

The clone is reset between samples (`checkout`, `clean -fd`) and returned
to its starting commit at the end, so the harness refuses a dirty tree.
Needs history: `git fetch --depth <N+1>` on a shallow clone first.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"


def git(repo: Path, *args: str, input: bytes | None = None) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], input=input,
                         capture_output=True, check=True)
    return out.stdout.decode("utf-8", "replace")


def scan(repo: Path, extra: list[str]) -> tuple[float, int, str]:
    env = dict(os.environ, PYTHONPATH=str(SRC))
    t = time.perf_counter()
    out = subprocess.run([sys.executable, "-m", "grounded.cli", "scan", ".", "--changed",
                          "--format", "json", "--no-color", "--fail-on", "never", *extra],
                         cwd=repo, env=env, capture_output=True)
    dt = time.perf_counter() - t
    if out.returncode not in (0, 1):
        raise RuntimeError(out.stderr.decode("utf-8", "replace")[-2000:])
    try:
        findings = len(json.loads(out.stdout))
    except ValueError:
        findings = -1
    err = out.stderr.decode("utf-8", "replace")
    mode = "broad: " + err.split("broad mode because ", 1)[1].split(";", 1)[0] \
        if "broad mode because " in err else "precise"
    return dt, findings, mode


def pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1))))
    return ordered[k]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("repo", type=Path)
    ap.add_argument("--commits", type=int, default=30)
    ap.add_argument("--no-index-cache", action="store_true")
    args = ap.parse_args()
    repo = args.repo.resolve()
    if git(repo, "status", "--porcelain", "--untracked-files=no").strip():
        print("bench/changed.py: worktree has uncommitted changes; refusing to reset it",
              file=sys.stderr)
        return 2
    start = git(repo, "rev-parse", "HEAD").strip()
    extra = ["--no-index-cache"] if args.no_index_cache else []
    commits = [c for c in git(repo, "rev-list", "--no-merges", f"--max-count={args.commits}",
                              "HEAD").split() if c]
    samples = []
    try:
        for sha in commits:
            try:
                parent = git(repo, "rev-parse", f"{sha}^").strip()
            except subprocess.CalledProcessError:
                continue  # shallow boundary
            git(repo, "checkout", "-q", "--detach", parent)
            scan(repo, extra)
            diff = subprocess.run(["git", "-C", str(repo), "diff", "--binary", parent, sha],
                                  capture_output=True, check=True).stdout
            try:
                git(repo, "apply", "--whitespace=nowarn", input=diff)
            except subprocess.CalledProcessError:
                git(repo, "checkout", "-q", ".")
                continue
            files = len([ln for ln in git(repo, "diff", "--name-only", "HEAD").splitlines() if ln])
            seconds, findings, mode = scan(repo, extra)
            samples.append({"commit": sha[:12], "files_changed": files,
                            "seconds": round(seconds, 3), "findings": findings, "mode": mode})
            git(repo, "checkout", "-q", ".")
            git(repo, "clean", "-qfd")
    finally:
        git(repo, "checkout", "-q", ".")
        git(repo, "clean", "-qfd")
        git(repo, "checkout", "-q", "--detach", start)
    secs = [s["seconds"] for s in samples]
    print(json.dumps({
        "repo": repo.name, "index_cache": not args.no_index_cache,
        "python": sys.version.split()[0], "samples": len(samples),
        "p50": round(statistics.median(secs), 3) if secs else None,
        "p95": round(pct(secs, 95), 3) if secs else None,
        "max": round(max(secs), 3) if secs else None,
        "runs": samples,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
