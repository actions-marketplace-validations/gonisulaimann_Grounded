"""ghost-export (experimental): public symbols nothing uses.
"""
from __future__ import annotations

import posixpath
import re

from ..models import FileFacts, Finding
from ..repo_index import RepoIndex
from ._shared import (
    _code_text,
    _dedupe,
    _is_dunder,
)


# ---------------------------------------------------------------- ghost-export
# EXPERIMENTAL, opt-in only. A public symbol nobody can reach: no
# importers anywhere, no use in its own file, no deliberate API marking.
# Libraries consume their API externally, so anything that looks like
# deliberate surface (__init__ modules, __all__, exports, Go-exported
# names, C without static info) is excluded, not flagged.

_GHOST_ALL = re.compile(r"__all__\s*=\s*\[[^\]]*\]")
_GHOST_TEST_FILE = re.compile(r"^(test_.*|.*_test)\.(py|go)$|\.(test|spec)\.[A-Za-z0-9]+$")

# Expected-output and generated paths: reachability cannot be judged for
# code a generator emits, so neither can "nobody can reach it". Covers
# svelte's `tests/snapshot/samples/*/_expected/`, jest/vitest
# `__snapshots__/`, and the usual codegen directory names.
_GHOST_GENERATED_PATH = re.compile(
    r"(^|/)(_expected|__snapshots__|snapshots|generated|codegen)(/|$)"
    r"|\.(gen|generated)\.[A-Za-z0-9]+$")


def _ghost_importers(symbol: str, own: str, index: RepoIndex) -> bool:
    for rel, names in index.file_imports.items():
        if rel != own and symbol in names:
            return True
    return False


def _ghost_used_in_sibling(name: str, rel: str, index: RepoIndex) -> bool:
    """Same-package cross-file use: Go/C call across files without imports,
    so same-file counting plus importers miss real uses (seen:
    debugPrintRoute called from gin.go, defined in debug.go). Only files
    in the same directory are consulted (Go package boundary; C
    translation-unit proximity), and only call-shaped occurrences count.
    Suppression-only: a comment mentioning the name cannot resurrect real
    dead code, it can only hide a finding.
    """
    root = getattr(index, "root", None)
    if root is None or not hasattr(index, "text_of"):
        return False
    want_dir = posixpath.dirname(rel)
    pat = re.compile(r"\b" + re.escape(name) + r"\s*\(")
    for f in getattr(index, "files", []):
        try:
            frel = f.relative_to(root).as_posix()
        except ValueError:
            continue
        if frel == rel or posixpath.dirname(frel) != want_dir:
            continue
        if pat.search(index.text_of(frel) or ""):
            return True
    return False


def _ghost_build_tagged_variants(name: str, index: RepoIndex) -> bool:
    """Mutually exclusive //go:build variants (binding.go vs
    binding_nomsgpack.go): flagging either as dead deletes a live build
    configuration. All definers constrained ⇒ unknowable which builds."""
    definers = index.symbol_files.get(name, set())
    if len(definers) < 2:
        return False
    if getattr(index, "root", None) is None or not hasattr(index, "text_of"):
        return False
    for drel in definers:
        text = index.text_of(drel)
        if text is None or not re.search(r"^\s*//go:build\b", text, re.MULTILINE):
            return False
    return True


def check_ghost_export(facts: FileFacts, index: RepoIndex) -> list[Finding]:
    """Public symbol with no importers, no in-file use, no API marking.

    Runs per defining file: each top-level public definition is checked
    against repo-wide importers plus same-file code use. Methods,
    dunders, package surface, exports, and (for Go) exported names are
    never candidates.
    """
    if facts.language not in ("python", "javascript", "go"):
        return []
    if _GHOST_GENERATED_PATH.search(facts.path):
        return []  # generated/expected output: reachability is unknowable
    findings: list[Finding] = []
    code = _code_text(facts)
    full_text = "\n".join(facts.lines)
    all_match = _GHOST_ALL.search(full_text)
    all_block = all_match.group(0) if all_match else ""
    for f in facts.functions:
        name = f.name
        if not name or name.startswith("_") or _is_dunder(name) or len(name) < 3:
            continue
        if name.startswith("test_") and _GHOST_TEST_FILE.search(posixpath.basename(facts.path)):
            continue  # framework-discovered entry points (pytest, go test)
        if getattr(f, "is_method", False):
            continue
        defline = facts.lines[f.lineno - 1] if 1 <= f.lineno <= len(facts.lines) else ""
        if facts.language == "javascript":
            if not re.match(r"^\s*(?:export\s+)?(?:async\s+)?(?:function\s+|class\s+|(?:const|let|var)\s+"
                            + re.escape(name) + r"\b)", defline):
                continue  # methods and object properties are not trackable here
            if re.search(r"^\s*export\b[^\n]*\b" + re.escape(name) + r"\b", full_text, re.MULTILINE):
                continue  # deliberate export surface
            if re.search(r"module\.exports\b[^\n]*\b" + re.escape(name) + r"\b", full_text):
                continue
        elif facts.language == "go":
            if not re.match(r"^\s*func\s+[A-Za-z_]", defline):
                continue  # methods carry a receiver between func and name
            if name[0].isupper():
                continue  # exported: consumed outside the repo by design
            if name in ("main", "init"):
                continue
        else:
            if posixpath.basename(facts.path) == "__init__.py":
                return []  # package surface file: everything here is API
            if re.search(r"['\"]" + re.escape(name) + r"['\"]", all_block):
                continue  # deliberate API via __all__
        if _ghost_importers(name, facts.path, index):
            continue
        if index.attr_used_by(name, facts.path):
            continue  # used as mod.name elsewhere (from pkg import mod)
        uses = len(re.findall(r"\b" + re.escape(name) + r"\b", code))
        if uses > 1:
            continue  # used in its own file (def line itself counts once)
        if facts.language in ("go", "c") and _ghost_used_in_sibling(name, facts.path, index):
            continue  # same-package cross-file call needs no import
        if _ghost_build_tagged_variants(name, index):
            continue  # mutually exclusive build variants, all constrained
        findings.append(Finding(
            path=facts.path, line=f.lineno, end_line=f.lineno,
            checker="ghost-export", severity="smell",
            title=f"`{name}` is public but never imported or used anywhere",
            claim=f"`{name}`",
            evidence=f"No file imports `{name}`, and it is used nowhere else in "
                     f"`{facts.path}` across {len(index.files)} indexed source files.",
            fix="Delete it, or mark the API surface explicitly (`__all__`/export).",
            confidence=0.7,
        ))
    return _dedupe(findings)
