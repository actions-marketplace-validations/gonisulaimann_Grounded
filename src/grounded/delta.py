"""Delta gating: baselines and changed-line filtering (stdlib only).

Two adoption mechanisms, composable:

- baseline: record today's findings to a committed JSON file; later runs
  report only findings NOT in the file. Fingerprints hash checker + path +
  claim + title (never line numbers), so unrelated edits that shift lines
  do not churn the baseline. Editing the offending line itself changes the
  claim and re-triggers the gate.
- changed lines: `git diff` selects added/modified lines; findings outside
  them are hidden, EXCEPT findings whose claim names a symbol the diff
  touches (rename fallout: the edit renamed it here, the lie lives
  there). The whole tree is still scanned (cross-file resolution
  needs the full index); filtering applies to reporting only. Only
  verified findings are ever shown: expansion changes relevance, never
  truth.

Outside a git repo, or when git cannot resolve the base, changed-line mode
is an error, never a silent full scan: a gate must not quietly change what
it gates.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from .models import Finding

BASELINE_VERSION = 1
DEFAULT_BASELINE_NAME = ".grounded-baseline.json"


def fingerprint(f: Finding) -> str:
    h = hashlib.sha256()
    h.update(b"grounded-baseline-v1\x00")
    h.update(f.checker.encode("utf-8") + b"\x00")
    h.update(f.path.encode("utf-8") + b"\x00")
    h.update(f.title.encode("utf-8") + b"\x00")
    h.update(f.claim.encode("utf-8"))
    return h.hexdigest()[:32]


def write_baseline(path: Path, findings: list[Finding]) -> dict[str, int]:
    """Write sorted, de-duplicated fingerprint entries. Returns
    {"added","removed","total"} relative to the existing file (0/0 when
    creating).

    Two findings can share a fingerprint (same rule, path, title and claim
    text on different lines), so the entries are de-duplicated before
    writing: otherwise the file grows with repeats and its length
    disagrees with the `total` the caller just printed (measured on a
    10k-file tree: 277 unique fingerprints, 284 findings, file of 284).
    """
    old = set()
    if path.exists():
        try:
            old = load_baseline(path)
        except ValueError:
            # A corrupt existing baseline is being overwritten anyway:
            # crash-looping on it would brick every later baseline write.
            # Stats treat everything as new, which is the honest answer.
            old = set()
    newset = {fingerprint(f) for f in findings}
    payload = {"version": BASELINE_VERSION, "fingerprints": sorted(newset)}
    path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    return {"added": len(newset - old), "removed": len(old - newset), "total": len(newset)}


def load_baseline(path: Path) -> set[str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"baseline not readable: {path} ({exc})") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"baseline is not valid JSON: {path}") from exc
    if not isinstance(data, dict) or data.get("version") != BASELINE_VERSION:
        raise ValueError(f"unsupported baseline format: {path}")
    fps = data.get("fingerprints")
    if not isinstance(fps, list) or not all(isinstance(x, str) for x in fps):
        raise ValueError(f"unsupported baseline format: {path}")
    return set(fps)


def split_baselined(findings: list[Finding], fps: set[str]) -> tuple[list[Finding], list[Finding]]:
    """Partition into (new, suppressed)."""
    new = [f for f in findings if fingerprint(f) not in fps]
    suppressed = [f for f in findings if fingerprint(f) in fps]
    return new, suppressed


class GitError(RuntimeError):
    pass


def _git(root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=60)
    except FileNotFoundError as exc:
        raise GitError("git is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError("git timed out") from exc
    if proc.returncode != 0:
        raise GitError((proc.stderr or proc.stdout or "git failed").strip().splitlines()[0][:160])
    return proc.stdout


def _resolve_base(root: Path, base: str) -> str:
    """Merge-base when both sides exist, else the ref as given. Never guesses."""
    try:
        mb = _git(root, "merge-base", base, "HEAD").strip()
        if mb:
            return mb
    except GitError:
        pass
    # verify the ref itself resolves (no --quiet: the error text is the message)
    _git(root, "rev-parse", "--verify", f"{base}^{{commit}}")
    return base


def _unquote_git_path(ref: str) -> str:
    """Undo git's quotepath quoting (`"caf\\303\\251.py"` for non-ASCII
    names when core.quotePath is on): the index keys paths unquoted, so
    a quoted hunk key never matches and --changed silently hides the
    finding."""
    if len(ref) >= 2 and ref.startswith('"') and ref.endswith('"'):
        try:
            return ref[1:-1].encode("latin-1").decode("unicode_escape").encode("latin-1").decode("utf-8")
        except (ValueError, UnicodeError):
            return ref[1:-1]
    return ref


def _parse_unified0(diff: str) -> dict[str, set[int]]:
    """Added new-file line numbers per path from `git diff -U0` output."""
    hunks: dict[str, set[int]] = {}
    cur: str | None = None
    new_ln = 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            cur = _unquote_git_path(line[6:])
            hunks.setdefault(cur, set())
            new_ln = 0
        elif line.startswith("@@") and cur is not None:
            try:
                plus = line.split("+", 1)[1].split(" @@", 1)[0]
                new_ln = int(plus.split(",")[0])
            except (ValueError, IndexError):
                cur = None
        elif cur is not None:
            if line.startswith("+") and not line.startswith("+++"):
                hunks[cur].add(new_ln)
                new_ln += 1
            elif line.startswith("-") and not line.startswith("---"):
                continue
            else:
                new_ln += 1
    return hunks


def changed_lines(root: Path, base: str) -> tuple[dict[str, set[int]], set[str]]:
    """Added lines by rel path, plus fully-changed untracked rel paths.

    Covers committed branch changes (merge-base with BASE through HEAD)
    UNION uncommitted worktree changes, so local edits are always gated.
    That includes the index: `git add` stages lines out of the unstaged
    diff, and a gate that hides staged lines would green-light exactly
    the change under review. Raises GitError outside a repo or when the
    base cannot be resolved.
    """
    _, hunks, untracked, _, _ = collect_changes(root, base)
    return hunks, untracked


def collect_changes(root: Path, base: str):
    """(ref, hunks, untracked, diff symbols, status) with one git call per
    fact: changed_lines, changed_symbols and changed_status each ran the
    same diffs again (13 git processes per `--changed`, 4 here; each
    worktree diff stats every tracked file)."""
    ref = _resolve_base(root, base)  # raises GitError outside a repository
    # One diff of the worktree against the base covers committed, staged
    # and unstaged changes alike, numbered by worktree lines (what findings
    # carry). Three separate diffs (base..HEAD, --cached, worktree) mixed
    # HEAD, index and worktree numbering and cost two more processes, each
    # stat()ing every tracked file.
    diff = _git(root, "diff", "-U0", "--no-color", "--no-ext-diff", "--relative", ref, "--")
    hunks = _parse_unified0(diff)
    symbols = _diff_symbols(diff)
    try:
        untracked = {p for p in _git(root, "ls-files", "-z", "--others",
                                     "--exclude-standard").split("\0") if p}
    except GitError:
        untracked = set()
    status = _name_status(root, ref)
    for rel in untracked:
        status[rel] = "A"
    return ref, hunks, untracked, symbols, status


def _name_status(root: Path, ref: str) -> dict[str, str]:
    out: dict[str, str] = {}
    fields = _git(root, "diff", "--name-status", "--no-renames", "--relative", "-z",
                  ref, "--").split("\0")
    for i in range(0, len(fields) - 1, 2):
        code, rel = fields[i][:1], fields[i + 1]
        if rel:
            out[rel] = {"A": "A", "D": "D"}.get(code, "M")
    return out


def changed_status(root: Path, base: str) -> tuple[str, dict[str, str]]:
    """(resolved base, {rel: "A"|"M"|"D"}) for every path under the scan
    root that differs between the base and the worktree (committed,
    staged and unstaged alike), untracked files as "A". Paths are
    scan-root-relative (`--relative`), like changed_lines. No rename
    detection: a rename is a delete plus an add."""
    ref = _resolve_base(root, base)
    out = _name_status(root, ref)
    for rel in _git(root, "ls-files", "-z", "--others", "--exclude-standard").split("\0"):
        if rel:
            out[rel] = "A"
    return ref, out


def grep_files(root: Path, tokens: set[str]) -> tuple[set[str], set[str]]:
    """(files naming any of `tokens` as a whole word, every file git
    knows) under root, scan-root-relative. `git grep -F -w` searches all
    tokens at once in C across threads; a Python alternation over the same
    tree took 4.7 s for ~100 names on cpython. Tracked and untracked
    (non-ignored) files only: callers search the rest themselves."""
    try:
        proc = subprocess.run(["git", "grep", "-l", "-z", "-I", "-F", "-w", "--untracked",
                               "-f", "-", "--", "."],
                              cwd=root, input="\n".join(sorted(tokens)) + "\n",
                              capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"git grep failed: {exc}") from exc
    if proc.returncode not in (0, 1):  # 1: no match
        raise GitError((proc.stderr or "git grep failed").strip()[:160])
    hits = {p for p in proc.stdout.split("\0") if p}
    known = {p for p in _git(root, "ls-files", "-z", "--cached", "--others",
                             "--exclude-standard").split("\0") if p}
    return hits, known


def base_texts(root: Path, ref: str, rels: list[str]) -> dict[str, str]:
    """Text of each rel at ref (scan-root-relative paths), one
    `git cat-file --batch` process for all of them. Paths absent at ref
    are simply missing from the result."""
    if not rels:
        return {}
    wanted = [r for r in rels if "\n" not in r]
    request = "".join(f"{ref}:./{r}\n" for r in wanted).encode("utf-8")
    try:
        proc = subprocess.run(["git", "cat-file", "--batch"], cwd=root, input=request,
                              capture_output=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"git cat-file failed: {exc}") from exc
    if proc.returncode != 0:
        raise GitError(proc.stderr.decode("utf-8", "replace").strip()[:160] or "git cat-file failed")
    data = proc.stdout
    out: dict[str, str] = {}
    pos = 0
    for rel in wanted:
        nl = data.find(b"\n", pos)
        if nl < 0:
            break
        header = data[pos:nl].split()
        pos = nl + 1
        if len(header) == 3 and header[1] == b"blob":
            size = int(header[2])
            out[rel] = data[pos:pos + size].decode("utf-8", errors="ignore")
            pos += size + 1  # content is followed by one newline
        elif len(header) == 3:
            pos += int(header[2]) + 1  # a tree or other object: skip it
    return out


def _diff_symbols(diff: str) -> set[str]:
    """Identifiers (len>=3) on added/removed diff lines, headers excluded."""
    out: set[str] = set()
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+") or line.startswith("-"):
            for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", line[1:]):
                if len(tok) >= 3:
                    out.add(tok)
    return out


def changed_symbols(root: Path, base: str) -> set[str]:
    """Symbols touched by branch changes plus uncommitted worktree edits
    (staged and unstaged alike)."""
    return collect_changes(root, base)[3]


def _claim_symbols(finding: Finding) -> set[str]:
    return {t for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", finding.claim or "") if len(t) >= 3}


def changed_file_filter(hunks: dict[str, set[int]], untracked: set[str],
                        symbols: set[str] | frozenset = frozenset()):
    """Predicate (rel, text) -> bool: can this file hold a finding that
    filter_changed would keep? Changed and untracked files, plus any file
    whose text names a diff-touched symbol (rename fallout). A strict
    superset of what filter_changed keeps, so checking only these files
    changes speed, never output."""
    # \b under re.ASCII is exactly the [A-Za-z0-9_] boundary (every symbol
    # starts and ends with a word character), and lets the engine skip the
    # per-position lookbehind.
    sym_re = (re.compile(r"\b(?:" + "|".join(
        re.escape(s) for s in sorted(symbols, key=len, reverse=True)) + r")\b", re.ASCII)
        if symbols else None)
    # A few symbols: a substring test rejects most files at C speed before
    # the regex runs (measured 14x on cpython for one rare identifier).
    # Many symbols: the single alternation is cheaper than k scans.
    literals = tuple(symbols) if 0 < len(symbols) <= 4 else ()

    def check(rel: str, text: str) -> bool:
        if rel in hunks or rel in untracked:
            return True
        if sym_re is None:
            return False
        if literals and not any(s in text for s in literals):
            return False
        return bool(sym_re.search(text))
    return check


def filter_changed(findings: list[Finding], hunks: dict[str, set[int]], untracked: set[str],
                   symbols: set[str] | frozenset = frozenset()) -> list[Finding]:
    """Keep findings on added/modified lines, in fully-untracked files, or
    naming a diff-touched symbol (rename fallout on untouched lines)."""
    kept: list[Finding] = []
    for f in findings:
        if f.path in untracked:
            kept.append(f)
            continue
        lines = hunks.get(f.path)
        if lines and any(ln in lines for ln in range(f.line, f.end_line + 1)):
            kept.append(f)
            continue
        if symbols and _claim_symbols(f) & set(symbols):
            kept.append(f)
    return kept
