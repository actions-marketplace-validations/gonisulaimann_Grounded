"""Directory scanner: collect files, build index, run checkers."""
from __future__ import annotations

import os
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from .checkers import CHECKERS
from .config import Config, DEFAULT_SUFFIXES
from .models import FileFacts, Finding
from .parsers import parse_file
from .repo_index import RepoIndex

_SUPPRESS = re.compile(r"grounded-disable\s*:\s*([A-Za-z0-9_][A-Za-z0-9_\-, ]*)")


def apply_suppressions(findings: list[Finding], facts_by_path: dict[str, FileFacts]) -> tuple[list[Finding], int]:
    """Honor `# grounded-disable: <id>[, ...]` / `// grounded-disable: ...`.

    A marker on any line of a finding's [line, end_line] span suppresses it
    for the listed checker ids (`all` matches everything). Returns
    (kept, suppressed_count). Explicit, local, reviewable: the decision to
    accept a finding lives next to the finding.
    """
    kept: list[Finding] = []
    suppressed = 0
    for f in findings:
        facts = facts_by_path.get(f.path)
        if facts is None:
            kept.append(f)
            continue
        disabled: set[str] = set()
        for ln in range(f.line, max(f.line, f.end_line) + 1):
            if 1 <= ln <= len(facts.lines):
                m = _SUPPRESS.search(facts.lines[ln - 1])
                if m:
                    disabled.update(x.strip() for x in m.group(1).split(",") if x.strip())
        if f.checker in disabled or "all" in disabled:
            suppressed += 1
            continue
        kept.append(f)
    return kept, suppressed


def collect_files(root: Path, config: Config) -> list[Path]:
    out: list[Path] = []
    root = root.resolve()
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            entries = sorted(cur.iterdir())
        except OSError:
            continue
        for e in entries:
            name = e.name
            if e.is_dir():
                if name in config.ignore_dirs or name.startswith(".") and name in (".git", ".hg", ".svn"):
                    continue
                # always skip hidden cache-ish dirs
                if name in {".git", "__pycache__", "node_modules", ".venv"}:
                    continue
                stack.append(e)
            elif e.is_file():
                if name in config.ignore_files:
                    continue
                if e.suffix.lower() in DEFAULT_SUFFIXES:
                    # skip minified bundles
                    if e.suffix.lower() == ".js" and (name.endswith(".min.js") or name.endswith(".bundle.js")):
                        continue
                    # v2: TypeScript declaration files are ambient type
                    # surface, not implementation; comment heuristics
                    # misfire on them (proven: axios index.d.ts).
                    if name.endswith(".d.ts") or name.endswith(".d.cts") or name.endswith(".d.mts"):
                        continue
                    out.append(e)
    return sorted(out)


_INDEX: RepoIndex | None = None


def _init_worker(index: RepoIndex) -> None:
    global _INDEX
    _INDEX = index


def _scan_one(args: tuple[str, str, list[str]]) -> tuple[FileFacts | None, list[Finding]]:
    """Parse one file and run enabled checkers. Top-level for pickling.

    The repo index is shared per worker via initializer (not per task):
    pickling it with every task made parallel scans slower than serial.
    """
    rel, text, enabled = args
    facts = parse_file(Path(rel), rel, text)
    if facts is None:
        return None, []
    findings: list[Finding] = []
    for checker_id in enabled:
        fn = CHECKERS.get(checker_id)
        if fn is None:
            continue
        try:
            for fd in fn(facts, _INDEX) or []:
                findings.append(fd)
        except Exception:
            # A checker must never crash a scan; skip pathological files.
            continue
    return facts, findings


def default_jobs(n_files: int) -> int:
    if n_files < 32:
        return 1
    return min(32, (os.cpu_count() or 4) + 4)


def scan_root(root: Path, config: Config, jobs: int | None = None) -> tuple[list[Finding], list[FileFacts], RepoIndex]:
    global _INDEX
    files = collect_files(root, config)
    resolved = root.resolve()
    if jobs is None:
        jobs = default_jobs(len(files))
    jobs = max(1, min(jobs, len(files) or 1))
    enabled = sorted(config.enabled)
    # Single disk pass: texts feed both the index build and the workers.
    texts: dict[str, str] = {}
    rels: dict[str, str] = {}
    for f in files:
        try:
            rel = f.relative_to(resolved).as_posix()
        except ValueError:
            rel = f.name
        try:
            texts[str(f)] = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rels[str(f)] = rel
    index = RepoIndex(resolved, [f for f in files if str(f) in texts], texts)
    payloads = [(rels[k], v, enabled) for k, v in texts.items() if k in rels]
    facts_list: list[FileFacts] = []
    findings: list[Finding] = []
    if jobs == 1:
        _INDEX = index
        results = [_scan_one(p) for p in payloads]
    else:
        chunksize = max(1, len(payloads) // (jobs * 8))
        with ProcessPoolExecutor(max_workers=jobs, initializer=_init_worker, initargs=(index,)) as pool:
            results = list(pool.map(_scan_one, payloads, chunksize=chunksize))
    for facts, file_findings in results:
        if facts is None:
            continue
        facts_list.append(facts)
        findings.extend(file_findings)
    findings.sort(key=lambda x: (x.path, x.line, x.checker))
    return findings, facts_list, index
