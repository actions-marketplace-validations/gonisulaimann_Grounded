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
    def __init__(self, root: Path, files: list[Path], texts: dict[str, str] | None = None):
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
        # Optional pre-read contents (abs path string -> text) so callers
        # that already read the tree skip a second disk pass.
        self._texts = texts or {}
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
            if suffix == ".py":
                self._index_python(text, rel)
            elif suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"}:
                self._index_js(text, rel)
            elif suffix == ".go":
                self._index_go(text, rel)
            elif suffix in {".c", ".h"}:
                self._index_c(text, rel)
        self.all_symbols = set(self.py_symbols) | set(self.js_symbols) | set(self.go_symbols) | set(self.c_symbols)
        for s in self.all_symbols:
            self.lower_map.setdefault(s.lower(), set()).add(s)

    def _record(self, target: set[str], name: str, rel: str) -> None:
        target.add(name)
        self.symbol_files.setdefault(name, set()).add(rel)

    def _index_python(self, text: str, rel: str) -> None:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self._record(self.py_symbols, node.name, rel)
        # v2: module-level assigned names (gettext_lazy = lazy(...),
        # constants, singletons). A comment referencing them is not
        # dangling. Restricted to module level on purpose: function locals
        # must NOT leak repo-wide (precision direction).
        for node in getattr(tree, "body", []):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        self._record(self.py_symbols, t.id, rel)
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name):
                    self._record(self.py_symbols, node.target.id, rel)

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
        # export { a, b } / module.exports = { ... }
        for m in re.finditer(r"export\s*\{\s*([^}]+)\}", text):
            for part in m.group(1).split(","):
                name = part.strip().split(" as ")[0].strip().split(" ")[0].strip()
                if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", name or ""):
                    self._record(self.js_symbols, name, rel)
        for m in re.finditer(r"module\.exports\s*=\s*\{([^}]+)\}", text):
            for part in m.group(1).split(","):
                name = part.strip().split(":")[0].strip()
                if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", name or ""):
                    self._record(self.js_symbols, name, rel)
        for m in re.finditer(r"module\.exports\s*=\s*\{([^}]+)\}", text):
            for part in m.group(1).split(","):
                name = part.strip().split(":")[0].strip()
                if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", name or ""):
                    self.js_symbols.add(name)

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
