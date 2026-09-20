"""Autofix, narrow by design.

- File references: only unambiguous same-basename matches, comments only.
- Symbol references: stale-symbol lies with EXACTLY ONE rename
  candidate that is both similar (ratio >= 0.75) and scope-proximate
  (same directory preferred; cross-directory candidates considered only
  when nothing in-directory qualifies). Pure string similarity cannot
  disambiguate renames (measured: correct and wrong targets 0.015 apart),
  so scope does the deciding and ties mean no touch.
- Docstring findings carry the docstring's opening line, not the claim's
  line, so line-based rewriting there would be unsafe and is skipped.
Everything else is reported, never touched. `--dry-run` previews.
"""
from __future__ import annotations

import difflib
from pathlib import Path

from .models import Finding
from .repo_index import RepoIndex


def file_fix_candidates(findings: list[Finding], root: Path) -> list[tuple[Finding, str, int]]:
    """Return (finding, replacement rel path, 1-based line) for safe fixes."""
    by_base: dict[str, list[str]] = {}
    for p in root.rglob("*"):
        if p.is_file():
            by_base.setdefault(p.name, []).append(p.relative_to(root).as_posix())
    out: list[tuple[Finding, str, int]] = []
    seen: set[tuple[str, int, str]] = set()
    for f in findings:
        if f.checker != "stale-file-ref":
            continue
        key = (f.path, f.line, f.claim)
        if key in seen:
            continue
        seen.add(key)
        base = f.claim.strip().split("/")[-1]
        matches = by_base.get(base, [])
        if len(matches) != 1:
            continue
        target = Path(root / f.path)
        try:
            lines = target.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        # comment findings: claim lives on [line, end_line]; docstring
        # findings reuse the docstring's opening line: unsafe, skip unless
        # the claim text is actually on the reported line.
        hit = next((ln for ln in range(f.line, f.end_line + 1)
                    if 1 <= ln <= len(lines) and f.claim in lines[ln - 1]), None)
        if hit is None:
            continue
        out.append((f, matches[0], hit))
    return out


def symbol_fix_candidates(
    findings: list[Finding], root: Path, index: RepoIndex
) -> list[tuple[Finding, str, str, int]]:
    """Return (finding, old segment, new segment, 1-based line) for safe
    symbol renames. Backticked claims and verb-anchored bare calls both
    qualify; uniqueness plus scope proximity do the safety work."""
    out: list[tuple[Finding, str, str, int]] = []
    seen: set[tuple[str, int, str]] = set()
    for f in findings:
        if f.checker != "stale-symbol-ref":
            continue
        raw = f.claim.strip()
        backticked = raw.startswith("`") and raw.endswith("`")
        inner = raw[1:-1] if backticked else raw
        is_call = inner.endswith("()")
        base = inner[:-2] if is_call else inner
        base = base.split(".")[-1]
        key = (f.path, f.line, f.claim)
        if key in seen:
            continue
        seen.add(key)
        scored: list[tuple[float, str, bool]] = []
        for cand in index.all_symbols:
            if cand == base:
                continue
            ratio = difflib.SequenceMatcher(None, base, cand).ratio()
            if ratio < 0.75:
                continue
            same_dir = _same_dir(root, f.path, cand, index)
            scored.append((ratio, cand, same_dir))
        if not scored:
            continue
        in_dir = [s for s in scored if s[2]]
        pool = in_dir or scored
        if len(pool) != 1:
            continue  # ambiguous: report, never touch
        out.append((f, base, pool[0][1], _claim_line(root, f)))
    return [(f, o, n, ln) for f, o, n, ln in out if ln is not None]


def _same_dir(root: Path, claim_path: str, cand: str, index: RepoIndex) -> bool:
    """Whether the candidate is defined in the claiming file's directory."""
    claim_dir = str(Path(claim_path).parent)
    for rel in _defining_files(cand, index):
        if str(Path(rel).parent) == claim_dir:
            return True
    return False


def _defining_files(cand: str, index: RepoIndex) -> list[str]:
    return sorted(index.symbol_files.get(cand, []))


def _claim_line(root: Path, f: Finding) -> int | None:
    """Locate the claim occurrence; None when not safely locatable
    (docstring findings reuse the docstring's opening line)."""
    target = Path(root / f.path)
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    needle = f.claim.strip("`")
    for ln in range(f.line, f.end_line + 1):
        if 1 <= ln <= len(lines) and needle in lines[ln - 1]:
            return ln
    return None


def apply_fixes(root: Path, fixes: list[tuple[Finding, str, int]], dry_run: bool = False) -> int:
    """Apply (finding, replacement, line) file fixes. Returns files changed."""
    by_file: dict[str, list[tuple[Finding, str, int]]] = {}
    for f, replacement, ln in fixes:
        by_file.setdefault(f.path, []).append((f, replacement, ln))
    changed = 0
    for rel, items in by_file.items():
        target = Path(root / rel)
        try:
            original = target.read_text(encoding="utf-8")
        except OSError:
            continue
        lines = original.splitlines()
        touched = False
        for f, replacement, ln in items:
            if 1 <= ln <= len(lines) and f.claim in lines[ln - 1]:
                lines[ln - 1] = lines[ln - 1].replace(f.claim, replacement, 1)
                touched = True
        if touched:
            if not dry_run:
                text = "\n".join(lines)
                if original.endswith("\n"):
                    text += "\n"
                target.write_text(text, encoding="utf-8")
            changed += 1
    return changed


def apply_symbol_fixes(
    root: Path, fixes: list[tuple[Finding, str, str, int]], dry_run: bool = False
) -> int:
    """Apply (finding, old segment, new segment, line) renames.

    Replacement is scoped to the backticked claim span on the line, never
    to bare code: the claim text (with backticks) is located first, then
    only the old segment inside that span is rewritten.
    """
    by_file: dict[str, list[tuple[Finding, str, str, int]]] = {}
    for item in fixes:
        by_file.setdefault(item[0].path, []).append(item)
    changed = 0
    for rel, items in by_file.items():
        target = Path(root / rel)
        try:
            original = target.read_text(encoding="utf-8")
        except OSError:
            continue
        lines = original.splitlines()
        touched = False
        for f, old_seg, new_seg, ln in items:
            if not (1 <= ln <= len(lines)):
                continue
            line = lines[ln - 1]
            span = f.claim.strip()
            at = line.find(span)
            if at == -1:
                continue
            head, body, tail = line[:at], line[at:at + len(span)], line[at + len(span):]
            if old_seg not in body:
                continue
            lines[ln - 1] = head + body.replace(old_seg, new_seg, 1) + tail
            touched = True
        if touched:
            if not dry_run:
                text = "\n".join(lines)
                if original.endswith("\n"):
                    text += "\n"
                target.write_text(text, encoding="utf-8")
            changed += 1
    return changed
