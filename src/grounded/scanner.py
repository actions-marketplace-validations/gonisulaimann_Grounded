"""Directory scanner: collect files, build index, run checkers."""
from __future__ import annotations

from pathlib import Path

from .checkers import CHECKERS
from .config import Config, DEFAULT_SUFFIXES
from .models import FileFacts, Finding
from .parsers import parse_file
from .repo_index import RepoIndex


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


def scan_root(root: Path, config: Config) -> tuple[list[Finding], list[FileFacts], RepoIndex]:
    files = collect_files(root, config)
    index = RepoIndex(root.resolve(), files)
    facts_list: list[FileFacts] = []
    findings: list[Finding] = []
    for f in files:
        try:
            rel = f.relative_to(root.resolve()).as_posix()
        except ValueError:
            rel = f.name
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        facts = parse_file(f, rel, text)
        if facts is None:
            continue
        facts_list.append(facts)
        for checker_id in sorted(config.enabled):
            fn = CHECKERS.get(checker_id)
            if fn is None:
                continue
            try:
                for fd in fn(facts, index) or []:
                    findings.append(fd)
            except Exception:
                # A checker must never crash a scan; skip pathological files.
                continue
    findings.sort(key=lambda x: (x.path, x.line, x.checker))
    return findings, facts_list, index
