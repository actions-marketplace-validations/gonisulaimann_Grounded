"""stale-entrypoint: manifest entry points that point nowhere.
"""
from __future__ import annotations

import json
import posixpath
import re

from ..models import FileFacts, Finding
from ..repo_index import RepoIndex
from ._shared import (
    _dedupe,
)
from .imports import (
    _effective_symbols,
    _js_exists_on_disk,
    _resolve_py_target,
)


# ---------------------------------------------------------------- stale-entrypoint
# EXPERIMENTAL, opt-in only. Install-time strings rot silently:
# pyproject [project.scripts] targets and package.json bin/main paths.
# Only in-repo claims are judged; build-output dirs (dist/, build/)
# stay silent (absent pre-publish is normal, not a lie).

_ENTRY_BUILD_DIRS = {"dist", "build", "out", "target", "esm", "cjs", "umd"}


def _entry_toml(text: str):
    try:
        import tomllib  # py3.11+
        return tomllib.loads(text)
    except ImportError:
        pass
    except ValueError:
        return None
    try:
        from ..toml_compat import loads as _compat_loads
        return _compat_loads(text)
    except Exception:
        return None


def _entry_line(lines: list[str], needle: str) -> int:
    for i, line in enumerate(lines, start=1):
        if needle and needle in line:
            return i
    return 1


def check_stale_entrypoint(facts: FileFacts, index: RepoIndex) -> list[Finding]:
    """Manifest entry points that point nowhere.

    pyproject.toml `[project.scripts]`/`[project.gui-scripts]`
    `name = "mod:func"`: the module must exist and define the function.
    package.json `bin`/`main`: the relative file must exist (outside
    build-output dirs). Malformed manifests stay silent, never guess.
    """
    if facts.language != "config":
        return []
    findings: list[Finding] = []
    base = posixpath.basename(facts.path)
    parent = posixpath.dirname(facts.path)
    if base == "pyproject.toml":
        data = _entry_toml("\n".join(facts.lines))
        if not isinstance(data, dict):
            return []
        scripts: dict = {}
        proj = data.get("project")
        if isinstance(proj, dict):
            for section in ("scripts", "gui-scripts"):
                part = proj.get(section)
                if isinstance(part, dict):
                    scripts.update(part)
        for name, target in scripts.items():
            if not isinstance(target, str):
                continue
            ref = target.split("[")[0].strip()
            mod, _, func = ref.partition(":")
            mod, func = mod.strip(), func.strip()
            if not mod or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", mod):
                continue
            targets = _resolve_py_target(index, facts.path, mod, 0)
            if targets is None:
                continue  # external package
            existing = [t for t in targets if t in index.rel_paths]
            lineno = _entry_line(facts.lines, target)
            if not existing:
                findings.append(Finding(
                    path=facts.path, line=lineno, end_line=lineno,
                    checker="stale-entrypoint", severity="lie",
                    title=f"Entry point `{name}` targets missing module `{mod}`",
                    claim=target,
                    evidence=f"No `{mod}` module file exists in this repo.",
                    fix="Fix the module path or remove the entry point.",
                    confidence=0.85,
                ))
                continue
            if func:
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", func):
                    continue
                if any(not index.knows_symbols(t) for t in existing):
                    continue  # unparsed target may provide it: unknowable
                provided = any(func in _effective_symbols(index, t) for t in existing)
                dynamic = any("__getattr__" in index.file_symbols.get(t, set())
                              or t in index.file_dynamic_ns for t in existing)
                if provided or dynamic:
                    continue
                findings.append(Finding(
                    path=facts.path, line=lineno, end_line=lineno,
                    checker="stale-entrypoint", severity="lie",
                    title=f"Entry point `{name}` targets `{mod}:{func}`, which is not defined there",
                    claim=target,
                    evidence=f"`{existing[0]}` exists but defines no `{func}`.",
                    fix=f"Fix the function name or remove the entry point.",
                    confidence=0.8,
                ))
    elif base == "package.json":
        try:
            data = json.loads("\n".join(facts.lines))
        except ValueError:
            return []
        if not isinstance(data, dict):
            return []
        jobs: list[tuple[str, str]] = []
        b = data.get("bin")
        if isinstance(b, str):
            jobs.append(("bin", b))
        elif isinstance(b, dict):
            for k, v in b.items():
                if isinstance(v, str):
                    jobs.append((f"bin:{k}", v))
        m = data.get("main")
        if isinstance(m, str):
            jobs.append(("main", m))
        for label, target in jobs:
            t = target.strip()
            if not t or t.startswith(("http://", "https://")):
                continue
            if not (t.startswith("./") or t.startswith("../") or t.startswith("/")):
                continue  # bare package ref: external
            norm = posixpath.normpath(posixpath.join(parent, t.lstrip("/")) if parent else t)
            norm = norm[2:] if norm.startswith("./") else norm
            if norm in index.rel_paths:
                continue
            # Node resolves `main` like require(): exact file, then
            # appended extensions, then a directory index. The index holds
            # only scanned sources, so check the disk (seen: vite's
            # `"main": "./inline"` -> inline.js, `"main": "./index.css"`).
            if label == "main" and _js_exists_on_disk(index, norm, "require"):
                continue
            if label == "main" and any(
                    (index.root / norm / ("index" + e)).is_file() for e in (".js", ".json", ".node")):
                continue
            if label.startswith("bin") and (index.root / norm).is_file():
                continue
            local = posixpath.normpath(t)
            local = local[2:] if local.startswith("./") else local
            if local.split("/")[0] in _ENTRY_BUILD_DIRS:
                continue  # build output absent pre-publish is normal
            lineno = _entry_line(facts.lines, target)
            findings.append(Finding(
                path=facts.path, line=lineno, end_line=lineno,
                checker="stale-entrypoint", severity="lie",
                title=f"package.json `{label}` points at missing file `{t}`",
                claim=target,
                evidence=f"No such file exists in this repo.",
                fix="Fix the path or remove the entry.",
                confidence=0.8,
            ))
    return _dedupe(findings)
