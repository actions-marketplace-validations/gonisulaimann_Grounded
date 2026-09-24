"""phantom-package (experimental): imports no manifest declares.
"""
from __future__ import annotations

import re

from ..models import FileFacts, Finding
from ..repo_index import RepoIndex, _norm_dist
from ._shared import (
    _STDLIB_MODULES,
    _dedupe,
)
from .imports import (
    _resolve_py_target,
)


# ---------------------------------------------------------------- phantom-package
# EXPERIMENTAL, opt-in only. Imports that run locally (installed in the
# author's environment) but are declared nowhere: dead on clean CI and
# fertile ground for dependency confusion. Only absolute imports of
# names missing from every manifest flavor are flagged; stdlib,
# in-repo modules, and test-scoped groups (unioned) stay silent.
# Monorepos: root manifests only. This is hygiene drift, never a lie.

_NODE_BUILTINS = frozenset({
    "assert", "async_hooks", "buffer", "child_process", "cluster",
    "console", "constants", "crypto", "dgram", "diagnostics_channel",
    "dns", "domain", "events", "fs", "http", "http2", "https",
    "inspector", "module", "net", "os", "path", "perf_hooks", "process",
    "punycode", "querystring", "readline", "repl", "stream",
    "string_decoder", "sys", "timers", "tls", "trace_events", "tty",
    "url", "util", "v8", "vm", "wasi", "worker_threads", "zlib",
})


# Import name -> distribution name for the notorious mismatches
# (import yaml lives in distribution pyyaml). Curated, unambiguous
# only; ambiguous cases (Crypto, magic) stay silent via... nothing:
# they report, and the docs list this map. Keep it short on purpose.
_IMPORT_TO_DIST = {
    "yaml": "pyyaml",
    "PIL": "pillow",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4",
    "dateutil": "python-dateutil",
    "gi": "pygobject",
    "wx": "wxpython",
    "serial": "pyserial",
    "usb": "pyusb",
    "attr": "attrs",
    "jwt": "pyjwt",
    "jose": "python-jose",
    "dns": "dnspython",
    "nmap": "python-nmap",
    "ldap": "python-ldap",
    "consul": "python-consul",
    "socks": "pysocks",
    "rest_framework": "djangorestframework",
    "corsheaders": "django-cors-headers",
    "git": "gitpython",
    "jenkins": "python-jenkins",
    "magic": "python-magic",
}


def _phantom_py(facts: FileFacts, index: RepoIndex, declared: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[str] = set()
    _norm = _norm_dist
    jobs: list[tuple[str, int]] = []  # (top module, lineno)
    for module, level, _names, guarded, lineno in facts.from_imports:
        if guarded:
            continue  # compat import that may legitimately fail
        if module and not level:
            jobs.append((module.split(".")[0], lineno))
    for _alias, root in facts.imports.items():
        if root:
            top = root.split(".")[0]
            lines = _import_lines(facts, top)
            if lines and all(ln in facts.guarded_lines for ln in lines):
                continue  # every occurrence guarded: may legitimately fail
            jobs.append((top, lines[0] if lines else 1))
    for top, lineno in jobs:
        if not top or top in _STDLIB_MODULES or top in seen:
            continue
        seen.add(top)
        targets = _resolve_py_target(index, facts.path, top, 0)
        if targets is not None and any(t in index.rel_paths for t in targets):
            continue  # first-party (src-layout aware)
        if top in index.parent_tops:
            continue  # first-party above the scan root: not a distribution
        if _norm(top) in declared or _norm(_IMPORT_TO_DIST.get(top, top)) in declared:
            continue
        line = lineno if lineno > 1 else _import_line(facts, top)
        findings.append(Finding(
            path=facts.path, line=line, end_line=line,
            checker="phantom-package", severity="drift",
            title=f"`{top}` is imported but declared in no manifest",
            claim=f"import {top}",
            evidence=f"`{top}` is not stdlib, not in-repo, and matches no "
                     f"dependency in pyproject.toml, requirements files, or package.json.",
            fix=f"Declare it (or remove the import if the environment lied to you).",
            confidence=0.65,
        ))
    return findings


def _import_line(facts: FileFacts, top: str) -> int:
    lines = _import_lines(facts, top)
    return lines[0] if lines else 1


def _import_lines(facts: FileFacts, top: str) -> list[int]:
    out = []
    for i, line in enumerate(facts.lines, start=1):
        s = line.strip()
        if re.match(r"(?:from|import)\s+", s) and top in s:
            out.append(i)
    return out


def _phantom_js(facts: FileFacts, index: RepoIndex, declared: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    _norm = _norm_dist
    for spec, _kind, _default, _named, lineno in facts.js_imports:
        if spec.startswith(("./", "../", "/", "node:")):
            continue
        if spec in (".", ".."):
            continue  # parent/self-dir self-reference (require('..'))
        if spec.startswith("#"):
            continue  # package imports-map: unresolvable statically
        pkg = "/".join(spec.split("/")[:2]) if spec.startswith("@") else spec.split("/")[0]
        base = pkg[5:] if pkg.startswith("node:") else pkg
        if base in _NODE_BUILTINS:
            continue
        if _norm(pkg) in declared or _norm(base) in declared:
            continue
        # @types/X declares the host-provided module X (vscode, chrome):
        # unactionable as a runtimedep, so it stays silent. Trade-off, made
        # explicit: @types/lodash without lodash is missed the same way.
        type_pkg = "@types/" + (pkg[1:].replace("/", "__") if pkg.startswith("@") else pkg)
        if _norm(type_pkg) in declared:
            continue
        findings.append(Finding(
            path=facts.path, line=lineno, end_line=lineno,
            checker="phantom-package", severity="drift",
            title=f"`{pkg}` is imported but declared in no manifest",
            claim=f"import {pkg}",
            evidence=f"`{pkg}` matches no dependency in package.json.",
            fix=f"Declare it (or remove the import if the environment lied to you).",
            confidence=0.65,
        ))
    return findings


def check_phantom_package(facts: FileFacts, index: RepoIndex) -> list[Finding]:
    """Imports declared in no manifest (pyproject, requirements, package.json)."""
    if facts.language not in ("python", "javascript"):
        return []
    declared = index.declared_dependencies(facts.path)
    if declared is None:
        return []  # no manifest anywhere: nothing to judge against
    if facts.language == "python":
        return _dedupe(_phantom_py(facts, index, declared))
    return _dedupe(_phantom_js(facts, index, declared))
