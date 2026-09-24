"""stale-mock-ref: @patch/patch.object strings naming absent symbols.
"""
from __future__ import annotations

import re

from ..models import FileFacts, Finding
from ..repo_index import RepoIndex
from ._shared import (
    PYTHON_BUILTINS,
    _dedupe,
    _is_dunder,
)
from .imports import (
    _effective_symbols,
    _resolve_py_base,
    _resolve_py_target,
)


# ---------------------------------------------------------------- stale-mock-ref
# EXPERIMENTAL, opt-in only. Test doubles rot silently: @patch("a.b.C")
# names a symbol that no longer exists, and nothing fails until that
# test runs. Only in-repo module paths are judged; external strings,
# create=True, and unresolvable targets stay silent.

_MOCK_PATCH_STR = re.compile(
    r"(?:^|[\s(@.])(?:[A-Za-z_][A-Za-z0-9_.]*\.)?patch\s*\(\s*[\"']([^\"']+)[\"']")
_MOCK_PATCH_OBJECT = re.compile(
    r"(?:^|[\s(@.])(?:[A-Za-z_][A-Za-z0-9_.]*\.)?patch\.object\s*\(\s*"
    r"(?:[\"']([^\"']+)[\"']|([A-Za-z_][A-Za-z0-9_.]*))\s*,\s*[\"']([^\"']+)[\"']")


def _mock_split(path: str) -> tuple[str, str] | None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", path or ""):
        return None
    parts = path.split(".")
    if len(parts) < 2:
        return None
    return (".".join(parts[:-1]), parts[-1])


def _mock_provided(index: RepoIndex, target: str, symbol: str) -> bool:
    if target.endswith("__init__.py"):
        # A package provides its submodules as attributes once imported
        # (`from build import _ctx; patch.object(_ctx, ...)`, seen:
        # pypa/build's src/build/_ctx.py).
        pkg = target[: -len("__init__.py")]
        if pkg + symbol + ".py" in index.rel_paths or pkg + symbol + "/__init__.py" in index.rel_paths:
            return True
    provided = symbol in _effective_symbols(index, target)
    dynamic = ("__getattr__" in index.file_symbols.get(target, set())
               or target in index.file_dynamic_ns)
    return provided or dynamic


_IMPORT_AS = re.compile(
    r"^\s*import\s+(.+)$")


def _mock_import_map(facts: FileFacts, skip: set[int]) -> dict[str, tuple[str, int, str | None]]:
    """Bare head -> (module, level, orig): full dotted module, the name as
    defined there (alias resolved), level for relatives. Covers
    `from m import (X as) Y`, `import a.b as m`, and plain `import a`
    (top segment, usable as an absolute root).
    """
    out: dict[str, tuple[str, int, str | None]] = {}
    for module, level, names, _guarded, _lineno in facts.from_imports:
        for orig, alias in names:
            out[alias or orig] = (module or "", level, orig)
    for idx, line in enumerate(facts.lines, start=1):
        if idx in skip:
            continue
        m = _IMPORT_AS.match(line)
        if not m:
            continue
        for part in m.group(1).split(","):
            pm = re.fullmatch(r"\s*([A-Za-z_][A-Za-z0-9_.]*)\s+as\s+([A-Za-z_][A-Za-z0-9_]*)\s*", part)
            if pm:
                out[pm.group(2)] = (pm.group(1), 0, None)
    for alias, root in facts.imports.items():
        if root and alias not in out:
            out[alias] = (root, 0, None)
    return out


def _mock_resolve_path(index: RepoIndex, claimer: str, parts: list[str]
                       ) -> tuple[str, str, str] | None:
    """Progressive resolution of an absolute dotted mock path.

    Returns None when silent (external top, or a resolvable prefix
    provides the head: attribute chains, methods, builtins injected
    into module namespace), else (modfile, head, full) for the verdict.
    The longest file-prefix wins; attribute chains below a provided
    head are method gaps the snapshot cannot falsify.
    """
    if not parts or len(parts) < 2:
        return None
    top_targets = _resolve_py_target(index, claimer, parts[0], 0)
    if top_targets is None and parts[0] not in index.py_prefixes:
        return None  # external top-level: outside snapshot analysis
    for k in range(len(parts) - 1, 0, -1):
        mod = ".".join(parts[:k])
        targets = _resolve_py_target(index, claimer, mod, 0)
        if targets is None:
            continue
        existing = [t for t in targets if t in index.rel_paths]
        if not existing:
            # Namespace package portion (no __init__): unenumerable.
            prefix = "/".join(parts[:k])
            if prefix in index.dirs and any(
                    r == prefix or r.startswith(prefix + "/") for r in index.rel_paths):
                return None
            continue
        if not all(index.knows_symbols(t) for t in existing):
            return None  # unparsed target may provide it: unknowable
        head = parts[k]
        if (head in PYTHON_BUILTINS
                or any(_mock_provided(index, t, head) for t in existing)):
            return None
        modpath = "/".join(parts[:k])
        if (f"{modpath}/{head}.py" in index.rel_paths
                or f"{modpath}/{head}/__init__.py" in index.rel_paths
                or f"{modpath}/{head}" in index.dirs):
            return None  # head is itself a module: patching it is valid
        return (existing[0], head, ".".join(parts))
    return None


def check_stale_mock_ref(facts: FileFacts, index: RepoIndex) -> list[Finding]:
    """@patch / patch.object strings naming absent in-repo symbols.

    A mocked path that resolves to a real module file but names nothing
    there is a test that errors at runtime. Module paths that resolve
    nowhere stay silent (external); create=True opts out explicitly.
    """
    if facts.language != "python":
        return []
    findings: list[Finding] = []
    skip: set[int] = set()
    for c in facts.comments:
        for ln in range(c.line, c.end_line + 1):
            skip.add(ln)
    import_map = _mock_import_map(facts, skip)
    for idx, line in enumerate(facts.lines, start=1):
        if idx in skip or not line.strip():
            continue
        jobs: list[list[str]] = []  # absolute dotted paths, attr included
        for m in _MOCK_PATCH_STR.finditer(line):
            split = _mock_split(m.group(1))
            if split:
                jobs.append((split[0] + "." + split[1]).split("."))
        for m in _MOCK_PATCH_OBJECT.finditer(line):
            str_target, bare_target, attr = m.group(1), m.group(2), m.group(3)
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", attr or ""):
                continue
            if str_target:
                # patch.object("app.Foo", "meth"): the class is verified,
                # the method is a documented gap.
                split = _mock_split(str_target)
                if split:
                    jobs.append((split[0] + "." + split[1]).split("."))
            elif bare_target:
                parts = bare_target.split(".")
                entry = import_map.get(parts[0])
                if not entry:
                    continue
                mod, level, orig = entry
                # segs continues the object path below the imported name;
                # orig is that name as defined (alias resolved). A bare
                # top import (`import a` used as `a.b.X`) is already
                # absolute: don't re-prepend. The attr itself stays
                # unjudged (method/meta gap by design).
                if orig is not None:
                    tail = [orig] + parts[1:]
                elif mod and mod != parts[0]:
                    tail = mod.split(".") + parts[1:]  # import-as alias
                else:
                    tail = parts
                if level:
                    base = _resolve_py_base(facts.path, level)
                    if not base and level - 1 > len(facts.path.split("/")[:-1]):
                        continue
                    full = base + ((mod.split(".") if mod else []) + tail
                                   if orig is not None or (mod and mod != parts[0])
                                   else tail)
                else:
                    full = ((mod.split(".") if mod else []) + tail
                            if orig is not None or (mod and mod != parts[0])
                            else tail)
                jobs.append(full)
        if not jobs:
            continue
        if re.search(r"\bcreate\s*=\s*True\b", line):
            continue
        for full in jobs:
            if len(full[-1]) < 3 or _is_dunder(full[-1]):
                continue
            verdict = _mock_resolve_path(index, facts.path, full)
            if verdict is None:
                continue
            modfile, head, _full = verdict
            findings.append(Finding(
                path=facts.path, line=idx, end_line=idx,
                checker="stale-mock-ref", severity="lie",
                title=f"Mock patches `{_full}`: `{head}` not found in `{modfile}`",
                claim=f'"{_full}"',
                evidence=f"`{modfile}` exists but provides no `{head}`.",
                fix="Update the mock to the current name.",
                confidence=0.8,
            ))
    return _dedupe(findings)
