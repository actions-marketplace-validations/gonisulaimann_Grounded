"""Directory scanner: collect files, build index, run checkers."""
from __future__ import annotations

import json
import os
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from .checkers import CHECKERS
from .config import Config, DEFAULT_SUFFIXES
from .models import CheckerError, FileFacts, Finding
from .parsers import parse_file
from .repo_index import RepoIndex

CACHE_NAME = ".grounded-cache.json"
# v2 stores per-file checker errors alongside findings. A v1 entry encodes
# "no findings" for a file whose checker *crashed*, so replaying it would
# re-import the silent-clean bug this version exists to remove: old caches
# are rejected rather than trusted.
CACHE_VERSION = 2

_SUPPRESS = re.compile(r"grounded-disable\s*:\s*([A-Za-z0-9_][A-Za-z0-9_\-, ]*)")

_PROJECT_MARKERS = ("grounded.toml", ".grounded.toml", "pyproject.toml",
                    "package.json", ".git")


def project_root_for(start: Path, stop: Path | None = None) -> Path:
    """Nearest ancestor of `start` (inclusive) holding a project marker.

    A single-file scan must index the project the file belongs to, not
    just its parent directory: a parent-only snapshot manufactures
    absence claims about files it never looked at (the partial-snapshot
    rule). Falls back to `start` when no marker is found (bounded by
    `stop` when given, e.g. an MCP server root), so behavior on
    marker-less trees is unchanged.
    """
    first = start.resolve()
    cur = first
    limit = stop.resolve() if stop is not None else None
    # Never climb *to* the home directory or above it: a dotfiles repo at
    # `~` would turn every marker-less scan into an index of the whole home
    # tree. Starting there (scanning `~` itself) is still allowed.
    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        home = None
    while True:
        if home is not None and cur != first and (cur == home or cur in home.parents):
            break
        try:
            if any((cur / m).exists() for m in _PROJECT_MARKERS):
                return cur
        except OSError:
            break
        parent = cur.parent
        if parent == cur:
            break
        if limit is not None and (cur == limit or len(parent.parts) < len(limit.parts)):
            break
        cur = parent
    return first


def resolve_scan_scope(target: Path, stop: Path | None = None) -> tuple[Path, str | None]:
    """(index root, report prefix) for a scan target.

    The index always covers the target's whole project; the prefix scopes
    reporting to the target. Indexing only a subdirectory manufactures
    absence claims about everything outside it (a comment in `src/` naming
    a helper in `scripts/` read as a lie), so the verdict for a file must
    not depend on which directory the user happened to scan. The prefix is
    None when the target is the project root itself.
    """
    resolved = target.resolve()
    base = resolved if resolved.is_dir() else resolved.parent
    root = project_root_for(base, stop=stop)
    try:
        rel = resolved.relative_to(root).as_posix()
    except ValueError:
        return base, None
    return root, (None if rel in (".", "") else rel)


def in_scope(path: str, prefix: str | None) -> bool:
    """Whether a project-relative finding path falls under a scan prefix."""
    return prefix is None or path == prefix or path.startswith(prefix + "/")


def warn_unknown_suppressions(facts_list: list[FileFacts]) -> list[tuple[str, int, list[str]]]:
    """Suppression markers naming unknown checker ids.

    A typo'd id (`stale-symobl`) silently matches nothing, so the user
    believes a finding is suppressed while it still fires. Report
    (path, line, [unknown ids]); callers print to stderr without ever
    affecting the exit code.
    """
    out: list[tuple[str, int, list[str]]] = []
    for facts in facts_list:
        for ln, line in enumerate(facts.lines, start=1):
            m = _SUPPRESS.search(line)
            if not m:
                continue
            unknown = [x.strip() for x in m.group(1).split(",") if x.strip()]
            unknown = [u for u in unknown if u != "all" and u not in CHECKERS]
            if unknown:
                out.append((facts.path, ln, unknown))
    return out


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


def collect_tsconfigs(root: Path, config: Config) -> list[Path]:
    """tsconfig.json files under root (honoring ignore dirs)."""
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
            if e.is_dir():
                if e.name in config.ignore_dirs or e.name in {".git", "__pycache__", "node_modules", ".venv"}:
                    continue
                stack.append(e)
            elif e.is_file() and e.name == "tsconfig.json":
                out.append(e)
    return sorted(out)


def build_alias_zones(root: Path, tsconfigs: list[Path],
                      manual: dict[str, list[str]]
                      ) -> tuple[list[tuple[str, list[tuple[str, list[str]]]]], set[str]]:
    """([(zone dir rel, [(alias prefix, [replacement prefixes])])],
    tsconfig-excluded rel path prefixes).

    tsconfig zones apply to their subtree (longest dir first at lookup);
    manual config aliases apply repo-wide as fallback. Files a tsconfig
    `exclude`s are outside that project's own typecheck contract: import
    checks stay silent about them (a repo may keep templates/scaffolds
    there whose imports only resolve after codegen transplantation —
    seen: svelte's excluded scripts/process-messages/templates/).
    """
    from .tsconfig import alias_map, excluded_rel, load_tsconfig
    zones: list[tuple[str, list[tuple[str, list[str]]]]] = []
    excluded: set[str] = set()
    for cfg in tsconfigs:
        try:
            rel = cfg.relative_to(root).as_posix()
        except ValueError:
            continue
        zone_dir = rel.rsplit("/", 1)[0] if "/" in rel else ""
        mapping = alias_map(load_tsconfig(cfg))
        if mapping:
            zones.append((zone_dir, mapping))
        for prefix in excluded_rel(zone_dir, cfg):
            excluded.add(prefix)
    zones.sort(key=lambda z: -len(z[0]))
    if manual:
        zones.append(("", sorted(manual.items(), key=lambda kv: -len(kv[0]))))
    return zones, excluded


def collect_files(root: Path, config: Config, include_claim_surfaces: bool = False,
                  ) -> tuple[list[Path], set[str]]:
    """All scannable files, plus the ambient declaration paths.

    Markdown and manifests ride along only for rename mapping
    (impact/MCP blast_radius: query paths that never gate) or when their
    checkers run; default scans stay byte-identical. Declaration files
    (.d.ts/.d.mts/.d.cts) are never parsed as source — comment heuristics
    misfire on them (proven: axios index.d.ts) — but their *existence* is
    returned as rel paths so import checks can stay silent about ambient
    type modules without treating declarations as implementation."""
    out: list[Path] = []
    decls: set[str] = set()
    root = root.resolve()
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            # A symlinked directory is never followed: inside the root it
            # is a duplicate of a directory scanned under its own name
            # (workspace-alias symlinks — seen: OmniRoute's `@omniroute/`
            # -> `open-sse/`, doubling findings and poisoning alias
            # resolution), and outside the root it would pull a foreign
            # tree into the snapshot. A repo whose only copy of code sits
            # behind a symlink is out of scope by the same snapshot rule.
            if cur.resolve() != cur:
                continue
            entries = sorted(cur.iterdir())
        except OSError:
            continue
        for e in entries:
            name = e.name
            if e.is_dir():
                if name in config.ignore_dirs and not _is_source_package(e, name, config):
                    continue
                if name.startswith(".") and name in (".git", ".hg", ".svn"):
                    continue
                # always skip hidden cache-ish dirs
                if name in {".git", "__pycache__", "node_modules", ".venv"}:
                    continue
                # Agent worktrees: full second copies of the repo with
                # their own drifted docs and half-finished edits (seen:
                # OmniRoute's `.claude/worktrees/`, 4,027 of 4,458 findings
                # were duplicates). They are working state, not the tree
                # the repo ships.
                if name == "worktrees" and cur.name == ".claude":
                    continue
                stack.append(e)
            elif e.is_file():
                if name in config.ignore_files:
                    continue
                suffixes = DEFAULT_SUFFIXES
                if {"stale-doc-ref", "stale-cli-ref", "unclosed-fence", "stale-cli-flag"} & set(
                        config.enabled or ()):
                    # Markdown is collected only when one of its checkers
                    # runs. Default configs always enable unclosed-fence, so
                    # it participates on every default scan.
                    suffixes = DEFAULT_SUFFIXES | {".md", ".markdown", ".mdc"}
                if "stale-cli-flag" in (config.enabled or ()):
                    # reST, including Sphinx sources kept as .txt under
                    # docs/ (seen: django).
                    suffixes = suffixes | {".rst"}
                    if e.suffix.lower() == ".txt" and ("/docs/" in f"/{e.relative_to(root).as_posix()}"
                                                       or "/doc/" in f"/{e.relative_to(root).as_posix()}"):
                        suffixes = suffixes | {".txt"}
                want_cfg = "stale-entrypoint" in (config.enabled or ())
                if include_claim_surfaces:
                    # Rename mapping reads docs and manifests; query paths
                    # never gate, so collecting more changes nothing gated.
                    suffixes = suffixes | {".md", ".markdown", ".mdc", ".toml"}
                    want_cfg = True
                if e.suffix.lower() == ".pyi":
                    # Stubs are presence evidence only (compiled extension
                    # modules ship a .pyi and no .py: pydantic-core's
                    # `_pydantic_core`), never parsed as source.
                    decls.add(e.relative_to(root).as_posix())
                    continue
                if e.suffix.lower() in suffixes or (want_cfg and (
                        e.suffix.lower() == ".toml" or e.name == "package.json")):
                    # skip minified bundles
                    if e.suffix.lower() == ".js" and (name.endswith(".min.js") or name.endswith(".bundle.js")):
                        continue
                    # v2: TypeScript declaration files are ambient type
                    # surface, not implementation; comment heuristics
                    # misfire on them (proven: axios index.d.ts). Track
                    # their paths only (existence evidence, no parsing).
                    if name.endswith(".d.ts") or name.endswith(".d.cts") or name.endswith(".d.mts"):
                        decls.add(e.relative_to(root).as_posix())
                        continue
                    out.append(e)
    return sorted(out), decls


# Test-fixture data: files that exist to be *input* (formatter cases,
# snapshot outputs, intentionally broken sample projects). Their imports
# and comments are data, not claims about the repo: measured 60+ "lies"
# across prettier tests/format/, black tests/data/cases/, pydantic
# tests/mypy/outputs/, vite __tests__/fixtures/, all by design. They stay
# indexed (other files may reference them); only their own findings are
# withheld. Deliberately narrow: `tests/` alone is code, not data.
_FIXTURE_SEGMENTS = frozenset({"fixtures", "fixture", "__fixtures__", "testdata",
                               "test_data", "test-data", "__snapshots__"})
_TEST_ROOTS = frozenset({"tests", "test", "testing", "__tests__", "spec", "specs"})
_TEST_DATA_SEGMENTS = frozenset({"data", "cases", "format", "samples", "inputs",
                                 "outputs", "expected"})


def is_fixture_path(rel: str) -> bool:
    all_parts = rel.split("/")
    parts = all_parts[:-1]
    if any(p in _FIXTURE_SEGMENTS for p in parts):
        return True
    for i, p in enumerate(parts):
        if p not in _TEST_ROOTS:
            continue
        below = parts[i + 1:]
        if any(q in _TEST_DATA_SEGMENTS for q in below):
            return True
        # `broken_app/`, `broken_tag.py`: fixtures that are broken on
        # purpose so a test can assert the failure (seen: django's
        # `broken_app` and `broken_tag` test fixtures).
        stem = all_parts[-1].rsplit(".", 1)[0]
        if any(q.startswith("broken") for q in below + [stem]):
            return True
    return False


# Default ignore names that are also common package names: `coverage/` is
# coverage.py's source package, `build/` is pypa/build's (both used to be
# skipped wholesale as build output). A directory holding `__init__.py` is
# a package, not output, unless the user ignored the name explicitly.
_OUTPUT_NAMES_THAT_MAY_BE_PACKAGES = frozenset({"coverage", "build", "dist", "out"})


def _is_source_package(path: Path, name: str, config: Config) -> bool:
    if name not in _OUTPUT_NAMES_THAT_MAY_BE_PACKAGES:
        return False
    if name in getattr(config, "explicit_ignore_dirs", ()):
        return False
    try:
        return (path / "__init__.py").is_file()
    except OSError:
        return False


_INDEX: RepoIndex | None = None


def _init_worker(index: RepoIndex) -> None:
    global _INDEX
    _INDEX = index


def _scan_one(args: tuple[str, str, list[str]]) -> tuple[FileFacts | None, list[Finding], list[CheckerError]]:
    """Parse one file and run enabled checkers. Top-level for pickling.

    The repo index is shared per worker via initializer (not per task):
    pickling it with every task made parallel scans slower than serial.
    """
    rel, text, enabled = args
    facts = parse_file(Path(rel), rel, text)
    if facts is None:
        return None, [], []
    if is_fixture_path(rel):
        return facts, [], []
    findings: list[Finding] = []
    errors: list[CheckerError] = []
    for checker_id in enabled:
        fn = CHECKERS.get(checker_id)
        if fn is None:
            continue
        try:
            for fd in fn(facts, _INDEX) or []:
                findings.append(fd)
        except Exception as exc:
            # A checker must never crash a scan — but it must never be
            # silently skipped either. A crash returns no findings, which
            # reads exactly like a clean file in every summary, so record it
            # and let the caller fail instead of reporting `clean`.
            errors.append(CheckerError(checker_id, rel, f"{type(exc).__name__}: {exc}"))
    facts.__dict__.pop("_comment_line_map", None)  # checker-local cache, not a fact
    return facts, findings, errors


def _error_from_dict(d: dict) -> CheckerError:
    return CheckerError(checker=str(d.get("checker", "")), path=str(d.get("path", "")),
                        message=str(d.get("message", "")))


def _finding_from_dict(d: dict) -> Finding:
    return Finding(
        path=str(d.get("path", "")), line=int(d.get("line", 0)),
        end_line=int(d.get("end_line", d.get("line", 0))),
        checker=str(d.get("checker", "")), severity=str(d.get("severity", "")),
        title=str(d.get("title", "")), claim=str(d.get("claim", "")),
        evidence=str(d.get("evidence", "")), fix=str(d.get("fix", "")),
        confidence=float(d.get("confidence", 0.0)))


def load_cache(path: Path, enabled: set[str], version: str,
               ) -> dict[str, tuple[float, int, list[dict], list[dict]]]:
    """rel -> (mtime, size, [finding dicts], [checker error dicts]). Empty on
    any problem: a cache must never fail a scan, only accelerate it."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        return {}
    if data.get("tool") != version or sorted(data.get("enabled", [])) != sorted(enabled):
        return {}
    out: dict[str, tuple[float, int, list[dict]]] = {}
    files = data.get("files", {})
    if not isinstance(files, dict):
        return {}
    for rel, entry in files.items():
        if not isinstance(entry, dict):
            continue
        try:
            out[str(rel)] = (float(entry["mtime"]), int(entry["size"]),
                             list(entry["findings"]), list(entry.get("errors", [])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def save_cache(path: Path, enabled: set[str], version: str,
               entries: dict[str, tuple[float, int, list[dict], list[dict]]]) -> None:
    try:
        path.write_text(json.dumps({
            "version": CACHE_VERSION, "tool": version,
            "enabled": sorted(enabled),
            "files": {rel: {"mtime": mt, "size": sz, "findings": dicts,
                            "errors": errs}
                      for rel, (mt, sz, dicts, errs) in entries.items()},
        }), encoding="utf-8")
    except OSError:
        pass


def default_jobs(n_files: int) -> int:
    # Measured crossover (spawn cost vs per-file work): parallel loses at
    # 40 files (0.06s vs 0.14s), ties near 500, wins clearly by ~3000
    # (14.2s vs 6.4s on 10 cores). Threshold 512 sits past the tie with
    # margin for larger real-world files. Workers capped at 8: 12-way
    # oversubscription measured slower than 8-way on the same box.
    if n_files < 512:
        return 1
    return min(8, max(1, os.cpu_count() or 4))


def _suffix_of(rel: str) -> str:
    dot = rel.rfind(".")
    slash = rel.rfind("/")
    if dot > slash:
        return rel[dot:].lower()
    return ""


def scan_root(root: Path, config: Config, jobs: int | None = None,
              cache_path: Path | None = None, include_claim_surfaces: bool = False,
              checker_errors: list[CheckerError] | None = None,
              check_only=None,
              ) -> tuple[list[Finding], list[FileFacts], RepoIndex]:
    """Scan a tree. Pass `checker_errors` to collect checkers that raised.

    The collector is optional so that read-only callers keep working, but any
    caller that reports a verdict (CLI, MCP) must pass one: without it a
    crashed checker is indistinguishable from a checker that found nothing.
    """
    global _INDEX
    from . import __version__
    files, decls = collect_files(root, config, include_claim_surfaces=include_claim_surfaces)
    resolved = root.resolve()
    auto_jobs = jobs is None
    if jobs is None:
        jobs = default_jobs(len(files))
    jobs = max(1, min(jobs, len(files) or 1))
    enabled = sorted(config.enabled)
    cached: dict[str, tuple[float, int, list[dict], list[dict]]] = {}
    if cache_path is not None:
        cached = load_cache(cache_path, set(enabled), __version__)
    # Single disk pass: texts feed both the index build and the workers.
    # stat() rides along for cache validation (one syscall per file).
    texts: dict[str, str] = {}
    rels: dict[str, str] = {}
    stats: dict[str, tuple[float, int]] = {}
    for f in files:
        try:
            rel = f.relative_to(resolved).as_posix()
        except ValueError:
            rel = f.name
        try:
            st = f.stat()
            texts[str(f)] = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rels[str(f)] = rel
        stats[str(f)] = (st.st_mtime, st.st_size)
    alias_zones, tsconfig_excluded = build_alias_zones(
        resolved, collect_tsconfigs(resolved, config), config.path_aliases)
    index = RepoIndex(resolved, [f for f in files if str(f) in texts], texts,
                      alias_zones,
                      decl_paths=decls, tsconfig_excluded=tsconfig_excluded,
                      jobs=jobs)
    facts_list: list[FileFacts] = []
    findings: list[Finding] = []
    # fresh[rel] holds JSON-ready finding dicts for the cache write-back.
    fresh: dict[str, tuple[float, int, list[dict], list[dict]]] = {}
    payloads: list[tuple[str, str, list[str]]] = []
    for key, text in texts.items():
        rel = rels[key]
        mt, sz = stats[key]
        if check_only is not None and not check_only(rel, text):
            # Indexed, not checked: the caller will report nothing from
            # this file (`--changed`: no changed lines, no changed symbol).
            facts_list.append(FileFacts(path=rel, language="skipped",
                                        lines=text.splitlines()))
            continue
        hit = cached.get(rel)
        if hit is not None and hit[0] == mt and hit[1] == sz:
            for d in hit[2]:
                findings.append(_finding_from_dict(d))
            if checker_errors is not None:
                # Replayed, not dropped: a cached file's checkers did not run
                # this time, so its recorded failures must survive the hit.
                checker_errors.extend(_error_from_dict(d) for d in hit[3])
            facts_list.append(FileFacts(path=rel, language="cache",
                                        lines=text.splitlines()))
            fresh[rel] = (mt, sz, hit[2], hit[3])
        else:
            # A symlinked source file resolves its relative imports from
            # the link target's directory (Node and Python both follow
            # the link), so its claims cannot be judged at the link's
            # path: index it, check it where it really lives (seen: vite
            # playground/preserve-symlinks/module-a/linked.js).
            payloads.append((rel, text, [] if Path(key).is_symlink() else enabled))
    if auto_jobs:
        # The checker pool is sized by the files it will check, not the
        # tree: a `--changed` run over 3 files must not spawn 8 workers.
        jobs = max(1, min(jobs, default_jobs(len(payloads))))
    if jobs == 1:
        _INDEX = index
        results = [_scan_one(p) for p in payloads]
    else:
        chunksize = max(1, len(payloads) // (jobs * 8))
        # Workers get the index without its text buffers: each file's text
        # already travels in its own payload, and the few checkers that
        # need another file's raw text read it on demand (text_of).
        with ProcessPoolExecutor(max_workers=jobs, initializer=_init_worker,
                                 initargs=(index.worker_copy(),)) as pool:
            results = list(pool.map(_scan_one, payloads, chunksize=chunksize))
    for facts, file_findings, file_errors in results:
        if facts is None:
            continue
        facts_list.append(facts)
        findings.extend(file_findings)
        if checker_errors is not None:
            checker_errors.extend(file_errors)
        key = next((k for k, r in rels.items() if r == facts.path), None)
        if key is not None:
            fresh[facts.path] = (stats[key][0], stats[key][1],
                                 [f.to_dict() for f in file_findings],
                                 [e.to_dict() for e in file_errors])
    findings.sort(key=lambda x: (x.path, x.line, x.checker))
    if cache_path is not None:
        # Merge: fresh results overwrite, untouched cached entries persist
        # (deleted files simply stop being written).
        merged = dict(cached)
        merged.update(fresh)
        save_cache(cache_path, set(enabled), __version__, merged)
    return findings, facts_list, index
