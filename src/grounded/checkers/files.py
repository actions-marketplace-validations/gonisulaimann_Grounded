"""stale-file-ref: comments and docstrings claiming a missing in-repo path.
"""
from __future__ import annotations

import re

from ..models import FileFacts, Finding
from ..repo_index import RepoIndex
from ._shared import (
    PLACEHOLDER_PATH_HINTS,
    _FILE_REF,
    _ILLUSTRATIVE,
    _TICKET,
    _commented_code_line_set,
    _dedupe,
    _is_placeholder_path,
    _nearby_ticket,
)
from .imports import (
    _base_in_ignored_dir,
    _in_repo_scope,
)


def check_stale_file(facts: FileFacts, index: RepoIndex) -> list[Finding]:
    findings: list[Finding] = []
    dead = _commented_code_line_set(facts)

    def _scan(text: str, line: int, end: int, kind: str) -> None:
        if not text.strip():
            return
        text = re.sub(r"https?://\S+", "", text)
        # Ticket-anchored history notes ("used to be in X.py ... See #10355")
        # point outside on purpose, like ticketed symbol claims. The ticket
        # may sit on a neighboring comment line.
        if _TICKET.search(text) or _nearby_ticket(facts, line, end):
            return
        for m in _FILE_REF.finditer(text):
            ref = m.group(1)
            segs = [s.lower() for s in re.split(r"[/.]", ref)]
            if any(h in segs for h in PLACEHOLDER_PATH_HINTS):
                continue
            if _is_placeholder_path(ref) and not index.has_exact_path(ref):
                continue
            # Elided paths (`src/.../EndpointPageClient.tsx`) are shorthand the
            # author chose instead of a full path: the `...` IS the
            # placeholder. The segment split above cannot see it ("..."
            # becomes empty segments), so test the raw ref.
            if "..." in ref or "…" in ref:
                continue
            if "://" in ref:
                continue
            if not _in_repo_scope(ref, index):
                continue
            # A path under a directory the scan deliberately ignores (build
            # outputs, vendor trees, coverage) is never indexed, so its
            # existence cannot be judged — the same verdict the import arms
            # already give. These are typically generated or written at
            # runtime, not authored (measured on OmniRoute: `dist/docs/
            # openapi.yaml` named in a CLI's help text and `dist/index.cjs`
            # in a setup command were the dominant `stale-file-ref` shape).
            if _base_in_ignored_dir(ref):
                continue
            # v2: illustrative examples invent paths ("For example ...
            # ``django/templatetags/news/photos.py``"); a file ref
            # within ~120 chars after such a marker is an example, not a claim.
            if _ILLUSTRATIVE.search(text[max(0, m.start() - 120):m.start()]):
                continue
            if index.has_exact_path(ref) or index.exists_near(ref, facts.path):
                continue
            if _passes_through_file(index, ref):
                continue  # `pyproject.toml/.coveragerc.toml`: alternatives, not a path
            # "<project>'s path/to/file" names another project's tree
            # (seen: vite's "Copy from rolldown's packages/rolldown/src/...",
            # whose first segment collides with vite's own `packages/`).
            owner = re.search(r"\b([A-Za-z][\w.-]*)'s\s+`?$", text[max(0, m.start() - 60):m.start()])
            if owner and owner.group(1).lower() not in {n.lower() for n in index.root_package_names}:
                continue
            if index.is_gitignored(ref):
                continue  # generated after checkout: absence is expected
            same = index.same_named(ref)
            detail = ""
            if same:
                shown = ", ".join(f"`{s}`" for s in same[:3])
                more = f" (+{len(same) - 3} more)" if len(same) > 3 else ""
                detail = f" Same-named files exist: {shown}{more}."
            findings.append(Finding(
                path=facts.path, line=line, end_line=end,
                checker="stale-file-ref", severity="lie",
                title=f"{kind} references missing file `{ref}`",
                claim=ref,
                evidence=f"`{ref}` claims a path inside this repo "
                         f"(first segment matches the repo tree), but no such file exists.{detail}",
                fix="Update the path or remove the reference. List nearby files to find the rename.",
                confidence=0.85,
            ))

    for c in facts.comments:
        if c.line in dead and c.end_line in dead:
            continue
        _scan(c.text, c.line, c.end_line, "Comment")
    # docstrings too (minus indented literal blocks: listings of example
    # paths introduced by a colon, the reST convention for samples)
    for f in (facts.functions if facts.language != "go" else ()):  # see symbols.py
        if f.docstring:
            _scan(_drop_literal_blocks(f.docstring), f.docstring_lineno or f.lineno,
                  f.docstring_lineno or f.lineno, "Docstring")
    return _dedupe(findings)


def _passes_through_file(index: RepoIndex, ref: str) -> bool:
    """A path whose directory part names an existing *file* cannot exist;
    prose uses the slash for alternatives ("in pyproject.toml/.coveragerc.toml",
    seen: coverage.py's tests)."""
    parts = ref.strip("./").split("/")
    for i in range(1, len(parts)):
        try:
            if (index.root / "/".join(parts[:i])).is_file():
                return True
        except OSError:
            return False
    return False


def _drop_literal_blocks(doc: str) -> str:
    """Remove indented *listings* introduced by a line ending in `:`.

    Docstring listings ("Here are the file names as seen in an egg based
    distribution:" followed by indented one-path-per-line samples) show
    sample data, not claims about this tree (seen: pytest's
    `_iter_rewritable_modules`). Only blocks whose every line is a single
    bare token are dropped: Google-style `Args:` sections are prose and
    keep full checking.
    """
    lines = doc.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        if not line.strip().endswith(":"):
            continue
        intro = len(line) - len(line.lstrip())
        j = i
        while j < len(lines) and not lines[j].strip():
            j += 1
        block: list[str] = []
        k = j
        while k < len(lines) and (not lines[k].strip()
                                  or len(lines[k]) - len(lines[k].lstrip()) > intro):
            block.append(lines[k])
            k += 1
        body = [b.strip() for b in block if b.strip()]
        if body and all(" " not in b for b in body):
            i = k  # a pure listing: sample data
    return "\n".join(out)
