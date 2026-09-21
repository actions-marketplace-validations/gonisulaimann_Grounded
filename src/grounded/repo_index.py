"""Repository-wide index: symbols and files for cross-reference checks."""
from __future__ import annotations

import ast
import re
from pathlib import Path

_JS_FUNC_PATTERNS = [
    re.compile(r"^\s*function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\("),
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\("),
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][A-Za-z0-9_$]*)\s*=>"),
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?function\b"),
    re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)\b"),
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?([A-Za-z_$][A-Za-z0-9_$]*)\s*\([^)]*\)\s*\{"),
    re.compile(r"^\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*:\s*(?:async\s*)?function\b"),
    re.compile(r"^\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*:\s*(?:async\s*)?\([^)]*\)\s*=>"),
    re.compile(r"^\s*export\s+default\s+(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\("),
    re.compile(r"^\s*module\.exports\.([A-Za-z_$][A-Za-z0-9_$]*)\s*="),
    re.compile(r"^\s*(?:export\s+)?(?:interface|type|enum)\s+([A-Za-z_$][A-Za-z0-9_$]*)\b"),
]

_JS_METHOD_HINT = re.compile(r"^\s*(?:async\s+|static\s+|get\s+|set\s+)?([A-Za-z_$][A-Za-z0-9_$]*)\s*\(")


class RepoIndex:
    def __init__(self, root: Path, files: list[Path], texts: dict[str, str] | None = None,
                 alias_zones: list[tuple[str, list[tuple[str, list[str]]]]] | None = None):
        self.root = root
        self.files = files  # absolute paths
        self.py_symbols: set[str] = set()
        self.js_symbols: set[str] = set()
        self.go_symbols: set[str] = set()
        self.c_symbols: set[str] = set()
        self.all_symbols: set[str] = set()
        self.lower_map: dict[str, set[str]] = {}
        # Defining file per symbol (rel posix paths), for scope-proximate
        # rename suggestions in autofix. A name may have several definers.
        self.symbol_files: dict[str, set[str]] = {}
        # Defined names per file (rel posix path), for import resolution.
        self.file_symbols: dict[str, set[str]] = {}
        # From-imported names per file (re-export chains resolve through these).
        self.file_imports: dict[str, set[str]] = {}
        # Star imports per file: [(module, level)] for one-hop expansion.
        self.file_stars: dict[str, list[tuple[str | None, int]]] = {}
        # JS/TS export surface per file: exported names ('default' marks a
        # default export) and star re-export specifiers.
        self.file_exports: dict[str, set[str]] = {}
        self.file_export_stars: dict[str, list[str]] = {}
        # Files with a bare `export *` (external re-export): export set unknown.
        self.file_export_unknown: set[str] = set()
        # Optional pre-read contents (abs path string -> text) so callers
        # that already read the tree skip a second disk pass.
        self._texts = texts or {}
        # tsconfig/manual path-alias zones: [(zone dir rel, [(prefix, [replacements])])].
        self.alias_zones: list[tuple[str, list[tuple[str, list[str]]]]] = list(alias_zones or [])
        # relative posix paths + basenames for file-ref resolution
        self.rel_paths: set[str] = set()
        self.basenames: set[str] = set()
        self.dirs: set[str] = set()
        # v2: top-level names (repo root entries) for the "claims about this
        # repo" rule: file refs whose first segment is not a repo top-level
        # name (and not ./ ../ /) are external/framework namespaces, not lies.
        self.top_names: set[str] = set()
        try:
            for child in root.iterdir():
                if child.name in (".git", "__pycache__", "node_modules"):
                    continue
                self.top_names.add(child.name)
        except OSError:
            pass
        self._build()

    def _build(self) -> None:
        for f in self.files:
            try:
                rel = f.relative_to(self.root).as_posix()
            except ValueError:
                rel = f.name
            self.rel_paths.add(rel)
            self.rel_paths.add("./" + rel)
            self.basenames.add(f.name)
            parent = str(Path(rel).parent)
            if parent and parent != ".":
                parts = Path(rel).parts[:-1]
                acc = ""
                for p in parts:
                    acc = p if not acc else acc + "/" + p
                    self.dirs.add(acc)
            suffix = f.suffix.lower()
            if str(f) in self._texts:
                text = self._texts[str(f)]
            else:
                try:
                    text = f.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
            self._index_one(rel, suffix, text, rebuild=False)
        self._rebuild_unions()

    def _record(self, target: set[str], name: str, rel: str) -> None:
        target.add(name)
        self.symbol_files.setdefault(name, set()).add(rel)
        self.file_symbols.setdefault(rel, set()).add(name)

    def _index_one(self, rel: str, suffix: str, text: str, rebuild: bool = True) -> None:
        """(Re-)index a single file's contributions. Safe to call repeatedly:
        previous contributions for rel are forgotten first."""
        self._forget_no_rebuild(rel)
        if suffix == ".py":
            self._index_python(text, rel)
        elif suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"}:
            self._index_js(text, rel)
        elif suffix == ".go":
            self._index_go(text, rel)
        elif suffix in {".c", ".h"}:
            self._index_c(text, rel)
        if rebuild:
            self._rebuild_unions()

    def _rebuild_unions(self) -> None:
        self.all_symbols = set(self.py_symbols) | set(self.js_symbols) | set(self.go_symbols) | set(self.c_symbols)
        self.lower_map = {}
        for s in self.all_symbols:
            self.lower_map.setdefault(s.lower(), set()).add(s)

    def forget_file(self, rel: str) -> None:
        """Drop every index contribution from rel (rename/delete/close)."""
        self._forget_no_rebuild(rel)
        self._rebuild_unions()

    def _forget_no_rebuild(self, rel: str) -> None:
        old = self.file_symbols.pop(rel, set())
        self.file_imports.pop(rel, None)
        self.file_stars.pop(rel, None)
        self.file_exports.pop(rel, None)
        self.file_export_stars.pop(rel, None)
        self.file_export_unknown.discard(rel)
        for name in old:
            holders = self.symbol_files.get(name)
            if holders is not None:
                holders.discard(rel)
                if not holders:
                    del self.symbol_files[name]
                    for lang in (self.py_symbols, self.js_symbols, self.go_symbols, self.c_symbols):
                        lang.discard(name)

    def _index_python(self, text: str, rel: str) -> None:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return

        def _targets(t) -> list[str]:
            # Destructured assignment targets: `A, B = ...`, `(A, B) = ...`.
            if isinstance(t, ast.Name):
                return [t.id]
            if isinstance(t, (ast.Tuple, ast.List)):
                out: list[str] = []
                for e in t.elts:
                    out.extend(_targets(e))
                return out
            if isinstance(t, ast.Starred):
                return _targets(t.value)
            return []

        def _bind_assign(node) -> None:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    for name in _targets(t):
                        self._record(self.py_symbols, name, rel)
            elif isinstance(node, ast.AnnAssign):
                for name in _targets(node.target):
                    self._record(self.py_symbols, name, rel)

        def _bind_import(node) -> None:
            if isinstance(node, ast.Import):
                for a in node.names:
                    top = (a.name or "").split(".")[0]
                    self.file_imports.setdefault(rel, set()).add(a.asname or top)
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name == "*":
                        self.file_stars.setdefault(rel, []).append((node.module, node.level or 0))
                    else:
                        self.file_imports.setdefault(rel, set()).add(a.asname or a.name)

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self._record(self.py_symbols, node.name, rel)
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name != "*":
                        self.file_imports.setdefault(rel, set()).add(a.asname or a.name)
        # Module-level bindings: direct body plus recursive descent into
        # try/if bodies (compat shims nest try blocks and assign/import
        # there constantly). Never descends into functions or classes, so
        # locals stay local. Depth is bounded by the tree itself.
        _TRY = (ast.Try,) + ((ast.TryStar,) if hasattr(ast, "TryStar") else ())
        stack: list[list] = [getattr(tree, "body", [])]
        while stack:
            for node in stack.pop():
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Import, ast.ImportFrom)):
                    _bind_assign(node)
                    _bind_import(node)
                elif isinstance(node, ast.If):
                    stack.append(list(node.orelse))
                    stack.append(list(node.body))
                elif isinstance(node, _TRY):
                    for h in node.handlers:
                        stack.append(list(h.body))
                    stack.append(list(node.orelse))
                    stack.append(list(node.finalbody))
                    stack.append(list(node.body))

    def _index_go(self, text: str, rel: str) -> None:
        for line in text.splitlines():
            m = re.match(r"^\s*func\s+(?:\([^)]*\)\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(", line)
            if m:
                self._record(self.go_symbols, m.group(1), rel)
                continue
            t = re.match(r"^\s*type\s+([A-Za-z_][A-Za-z0-9_]*)\b", line)
            if t:
                self._record(self.go_symbols, t.group(1), rel)

    def _index_c(self, text: str, rel: str) -> None:
        from .parsers import c_top_level_names
        for name in c_top_level_names(text):
            self._record(self.c_symbols, name, rel)

    def _index_js(self, text: str, rel: str) -> None:
        for line in text.splitlines():
            for pat in _JS_FUNC_PATTERNS:
                m = pat.match(line)
                if m:
                    self._record(self.js_symbols, m.group(1), rel)
                    break
            em = re.match(
                r"^\s*export\s+(?:async\s+)?(?:function\*?\s+|class\s+|"
                r"(?:const|let|var)\s+|(?:abstract\s+class\s+)|"
                r"(?:interface|type|enum)\s+)([A-Za-z_$][A-Za-z0-9_$]*)", line)
            if em:
                self.file_exports.setdefault(rel, set()).add(em.group(1))
            if re.match(r"^\s*export\s+default\b", line):
                self.file_exports.setdefault(rel, set()).add("default")
        # export { a, b as c } / export type { T } / export * from './x'
        for m in re.finditer(r"export\s+(?:type\s+)?\{\s*([^}]+)\}", text):
            for part in m.group(1).split(","):
                part = part.strip()
                if not part:
                    continue
                bits = [b.strip() for b in part.split(" as ")]
                alias = bits[-1].split(":")[0].strip()
                if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", alias or ""):
                    self._record(self.js_symbols, bits[0].split(":")[0].strip() or alias, rel)
                    self.file_exports.setdefault(rel, set()).add(alias)
        for m in re.finditer(r"export\s*\*\s*from\s*['\"]([^'\"]+)['\"]", text):
            spec = m.group(1)
            self.file_export_stars.setdefault(rel, []).append(spec)
            if not spec.startswith("./") and not spec.startswith("../"):
                # Bare re-export (external package): this module's export
                # set is unknowable from the snapshot. Recorded so name
                # checks against it stay silent instead of guessing.
                self.file_export_unknown.add(rel)
        for m in re.finditer(r"module\.exports\s*=\s*\{([^}]+)\}", text):
            for part in m.group(1).split(","):
                name = part.strip().split(":")[0].strip()
                if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", name or ""):
                    self._record(self.js_symbols, name, rel)
                    self.file_exports.setdefault(rel, set()).add(name)
        for m in re.finditer(r"(?:exports|module\.exports)\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=", text):
            self._record(self.js_symbols, m.group(1), rel)
            self.file_exports.setdefault(rel, set()).add(m.group(1))
        # `module.exports = <expr>` (anything but an object literal, whose
        # keys are handled above) is a default export, named or not.
        if re.search(r"module\.exports\s*=(?![=>])\s*(?![{\s])", text):
            self.file_exports.setdefault(rel, set()).add("default")

    def has_symbol(self, name: str) -> bool:
        if not name:
            return False
        if name in self.all_symbols:
            return True
        # dotted: check last segment
        if "." in name:
            last = name.split(".")[-1]
            if last in self.all_symbols:
                return True
        return False

    def has_exact_path(self, ref: str) -> bool:
        """Exact-path existence only (no basename fallback).

        Basename matching hid moved files (`src/old/x.py` silently
        resolving via an unrelated `x.py`), which is precisely the
        rename/move signal the checker and autofix need. Same-named
        files elsewhere are reported as candidates, not resolutions.
        """
        r = ref.strip().strip("'\"`").lstrip("./")
        r = r.split("?")[0].split("#")[0]
        if not r:
            return False
        if r in self.rel_paths:
            return True
        for known in self.rel_paths:
            if known.endswith("/" + r) or known == r:
                return True
        candidate = self.root / r
        try:
            if candidate.exists():
                return True
        except OSError:
            pass
        return False

    def same_named(self, ref: str) -> list[str]:
        """Repo-relative paths sharing the referenced basename, sorted."""
        base = ref.strip().split("/")[-1]
        return sorted(p for p in self.rel_paths if p.split("/")[-1] == base and not p.startswith("./"))
