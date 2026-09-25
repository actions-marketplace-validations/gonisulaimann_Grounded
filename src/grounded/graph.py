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
        from .checkers import (_CONTRACT_DEPRECATION, _CONTRACT_LOCK,
                               _CONTRACT_NAME, _DOC_CALL, _MOCK_PATCH_OBJECT,
                               _MOCK_PATCH_STR, _doc_fence_blocks,
                               _entry_toml, _mock_split)
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
                # Contract frames name symbols bare (no backticks/parens):
                # deprecation targets and lock holders break on rename.
                if _CONTRACT_DEPRECATION.search(text):
                    for m in _CONTRACT_NAME.finditer(text):
                        raw = m.group(1).rstrip(".")
                        base = raw[:raw.index("(")].split(".")[-1] if raw.endswith(")") else raw.split(".")[-1]
                        if len(base) >= 3:
                            seen.add(base)
                for m in _CONTRACT_LOCK.finditer(text):
                    base = m.group(1).rstrip(".").split(".")[-1]
                    if len(base) >= 3:
                        seen.add(base)
            lang = getattr(facts, "language", "")
            if lang == "markdown":
                # Fenced code examples name symbols that break on rename.
                # Same block gating as the checker: illustrative blocks
                # stay out; everything called gets listed (query tool:
                # recall direction, never a verdict).
                for _lang, start, end in _doc_fence_blocks(facts.lines):
                    for line in facts.lines[start - 1:end - 1]:
                        for m in _DOC_CALL.finditer(line):
                            base = m.group(1).split(".")[-1].lstrip("$")
                            if len(base) >= 3:
                                seen.add(base)
            elif lang == "python":
                # Mock strings name symbols that break on rename.
                code_lines = {i for i in range(1, len(facts.lines) + 1)}
                for c in facts.comments:
                    for ln in range(c.line, c.end_line + 1):
                        code_lines.discard(ln)
                for lineno in sorted(code_lines):
                    line = facts.lines[lineno - 1]
                    for m in _MOCK_PATCH_STR.finditer(line):
                        split = _mock_split(m.group(1))
                        if split and len(split[1]) >= 3:
                            seen.add(split[1])
                    for m in _MOCK_PATCH_OBJECT.finditer(line):
                        attr = m.group(3)
                        if attr and len(attr) >= 3:
                            seen.add(attr)
                        if m.group(1):
                            split = _mock_split(m.group(1))
                            if split and len(split[1]) >= 3:
                                seen.add(split[1])
            elif lang == "config" and facts.path.endswith("pyproject.toml"):
                # Entrypoint targets break when the function is renamed.
                data = _entry_toml("\n".join(facts.lines))
                proj = data.get("project") if isinstance(data, dict) else None
                if isinstance(proj, dict):
                    for section in ("scripts", "gui-scripts"):
                        part = proj.get(section)
                        if isinstance(part, dict):
                            for target in part.values():
                                if not isinstance(target, str):
                                    continue
                                _mod, _, func = target.split("[")[0].strip().partition(":")
                                if func.strip() and len(func.strip()) >= 3:
                                    seen.add(func.strip())
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
