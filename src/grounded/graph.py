"""Claim graph (experimental): repository relationships as queryable edges.

Nodes: symbols, files. Edges: defines (file -> symbol), imports
(file -> symbol, best-effort original names), claims (file -> symbol
mentioned in its comments). Built from the same structures as the
checkers; adds no new parsing.

The question under test: does a relationship model answer anything the
per-file checker fan-out cannot? The falsifiable candidate is blast
radius: "what references X?" (definers, importers, comment claims).
If this proves to be re-walking with no new capability, delete it.
"""
from __future__ import annotations

import re


class ClaimGraph:
    def __init__(self, index, facts_by_path: dict) -> None:
        from .checkers import _BACKTICK_SYMBOL, _SYMBOL_CALL
        self.defines: dict[str, set[str]] = {}    # symbol -> {files}
        self.imported_by: dict[str, set[str]] = {}  # symbol -> {files}
        self.claimed_by: dict[str, set[str]] = {}  # symbol -> {files}
        for name, rels in index.symbol_files.items():
            self.defines.setdefault(name, set()).update(rels)
        for rel, names in index.file_imports.items():
            for name in names:
                self.imported_by.setdefault(name, set()).add(rel)
        for name, rels in index.symbol_files.items():
            for rel in rels:
                for user in index.attr_used_by(name, rel):
                    # from pkg import mod + mod.name(): a real importer,
                    # invisible to import tracking. The module import must
                    # be present, so same-named locals don't qualify.
                    self.imported_by.setdefault(name, set()).add(user)
        for rel, facts in facts_by_path.items():
            texts = [c.text for c in facts.comments]
            for f in facts.functions:
                if f.docstring:
                    texts.append(f.docstring)
            seen: set[str] = set()
            for text in texts:
                for m in _BACKTICK_SYMBOL.finditer(text):
                    raw = m.group(1)
                    base = raw[:-2].split(".")[-1] if raw.endswith("()") else raw.split(".")[-1]
                    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", base or "") and len(base) >= 3:
                        seen.add(base)
                for m in _SYMBOL_CALL.finditer(text):
                    seen.add(m.group(1).split(".")[-1])
            for name in seen:
                self.claimed_by.setdefault(name, set()).add(rel)

    def blast_radius(self, symbol: str) -> dict[str, list[str]]:
        """Everything touching a symbol: definers, importers, comment claims."""
        return {
            "symbol": symbol,
            "defined_in": sorted(self.defines.get(symbol, set())),
            "imported_by": sorted(self.imported_by.get(symbol, set())),
            "claimed_by": sorted(self.claimed_by.get(symbol, set())),
        }
