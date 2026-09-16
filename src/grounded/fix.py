"""Autofix, narrow by design: stale file references only.

A file-ref finding is fixed only when exactly one repo file shares the
referenced basename (unambiguous rename/move). Comment findings only:
docstring findings carry the docstring's opening line, not the claim's
line, so line-based rewriting there would be unsafe and is skipped.
Everything else is reported, never touched. `--dry-run` previews.
"""
from __future__ import annotations

from pathlib import Path

from .models import Finding


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


def apply_fixes(root: Path, fixes: list[tuple[Finding, str, int]], dry_run: bool = False) -> int:
    """Apply (finding, replacement, line) fixes. Returns files changed."""
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
