"""Reproducible pre-test-filter benchmark (stdlib only, pytest optional).

Scenario: an agent renames `fetch_user` to `get_user` but leaves one stale
comment and one stale import behind. Measures time-to-signal for:

  A. grounded, in-process (the agent-loop path: parse + check one file)
  B. grounded, cold CLI on the file
  C. pytest collection + ImportError failure (skipped if pytest missing)

Methodology: best of N runs, wall clock, same machine. pytest leg uses a
fixture package whose test module imports the removed name, so collection
itself fails, the fastest possible pytest failure. This compares a static
pre-filter against test execution honestly: tests catch everything,
grounded catches reference drift in milliseconds before you pay for tests.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SRC = REPO / "src"
N = int(__import__("os").environ.get("GROUNDED_BENCH_N", "7"))


def fixture(root: Path) -> None:
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "core.py").write_text(
        "def get_user(uid):\n    return uid\n", encoding="utf-8")
    (root / "pkg" / "views.py").write_text(
        "# Calls `fetch_user()` to load the profile.\n"
        "from .core import fetch_user\n\n\n"
        "def show(uid):\n    return fetch_user(uid)\n",
        encoding="utf-8")
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests" / "test_views.py").write_text(
        "from pkg.core import fetch_user\n\n\n"
        "def test_show():\n    assert fetch_user(1) == 1\n",
        encoding="utf-8")


def best(fn, n: int = N) -> float:
    times = sorted(fn() for _ in range(n))
    return times[n // 2]


def main() -> int:
    sys.path.insert(0, str(SRC))
    from grounded.config import Config
    from grounded.parsers import parse_file
    from grounded.repo_index import RepoIndex
    from grounded.scanner import collect_files

    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        fixture(root)

        def in_process() -> float:
            t0 = time.perf_counter()
            files = collect_files(root, Config())
            idx = RepoIndex(root, files)
            from grounded import checkers
            for f in files:
                facts = parse_file(f, f.relative_to(root).as_posix(),
                                   f.read_text(encoding="utf-8"))
                if facts is not None:
                    for cid in ("stale-symbol-ref", "stale-import"):
                        checkers.CHECKERS[cid](facts, idx)
            return (time.perf_counter() - t0) * 1000

        def cold_cli() -> float:
            env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(SRC)}
            t0 = time.perf_counter()
            subprocess.run(
                [sys.executable, "-m", "grounded.cli", "scan",
                 str(root / "pkg" / "views.py"), "--quiet", "--no-color"],
                capture_output=True, env=env, timeout=120)
            return (time.perf_counter() - t0) * 1000

        a = best(in_process)
        b = best(cold_cli)

        try:
            import pytest  # noqa: F401
            has_pytest = True
        except ImportError:
            has_pytest = False
        c = None
        if has_pytest:
            def pytest_fail() -> float:
                t0 = time.perf_counter()
                subprocess.run(
                    [sys.executable, "-m", "pytest", "-x", "-q",
                     "--no-header", "-p", "no:cacheprovider",
                     str(root / "tests")],
                    capture_output=True, cwd=str(root), timeout=600)
                return (time.perf_counter() - t0) * 1000
            c = best(pytest_fail, n=3)

        print("pre-test-filter benchmark (best of %d, wall clock)" % N)
        print(f"  A. grounded in-process, one file : {a:8.1f} ms")
        print(f"  B. grounded cold CLI, one file   : {b:8.1f} ms")
        if c is None:
            print("  C. pytest collection failure     : skipped (pytest not installed)")
        else:
            print(f"  C. pytest collection failure     : {c:8.1f} ms")
            print(f"     pre-filter speedup (C/A)      : {c / max(a, 0.01):8.1f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
