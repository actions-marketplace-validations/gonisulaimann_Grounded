"""stale-import: Python and JS/TS imports whose module or name is missing,
plus the module-resolution helpers other checkers share.
"""
from __future__ import annotations

import json
import posixpath
import re

from ..models import FileFacts, Finding
from ..repo_index import RepoIndex, _norm_dist
from ._shared import (
    IGNORED_DIR_NAMES,
    _dedupe,
)


def _in_repo_scope(ref: str, index: RepoIndex) -> bool:
    """v2: only judge claims about THIS repo's tree.

    Measured failure: template namespaces (flatpages/default.html),
    external-project sources (Modules/_sqlite/connection.c), dependency
    modules (MySQLdb/cursors.py), i18n patterns (en/LC_MESSAGES/x.po) were
    all flagged as "missing files". Rule: judge a ref only when it is
    explicitly relative (./ ../ /) or its first segment is a repo
    top-level name. Everything else is an external/namespace reference the
    snapshot cannot decide.
    """
    r = ref.strip()
    if r.startswith(("./", "../", "/")):
        return True
    first = r.lstrip("./").split("/")[0]
    return first in index.top_names


def _resolve_py_target(index: RepoIndex, claimer: str, module: str | None, level: int) -> list[str] | None:
    """Candidate module rel paths for an import, or None if unresolvable
    (stdlib, third-party, namespace packages: outside snapshot analysis).

    Relative (level>0) walks up from the claiming file. Absolute requires
    the top segment to be a repo root entry.
    """
    if level:
        base = _resolve_py_base(claimer, level)
        if not base and level - 1 > len(claimer.split("/")[:-1]):
            return None
        if not base and len(claimer.split("/")) > 1 and "__init__.py" not in index.rel_paths:
            # Climbs to the repo root, which is not a package: Python can
            # only resolve this after the file is copied into a package
            # (codegen templates, seen: transformers'
            # examples/modular-transformers, 123 findings). Unknowable.
            return None
        if module:
            base = base + module.split(".")
        prefix = "/".join(base)
    else:
        if not module:
            return None
        segs = module.split(".")
        if segs[0] in index.top_names:
            prefix = "/".join(segs)
        elif segs[0] in index.py_prefixes:
            # src/ and lib/ layouts: `import mypkg.x` lives at
            # `src/mypkg/x.py`. Without this, whole layouts resolve as
            # third-party and skip silently.
            prefix = index.py_prefixes[segs[0]] + "/" + "/".join(segs)
        else:
            return None
    if not prefix:
        return None
    return [prefix + ".py", prefix + "/__init__.py"]


def _dynamic_ns(index: RepoIndex, rel: str) -> bool:
    return rel in index.file_dynamic_ns or rel in index.file_replaces_self


def _effective_symbols(index: RepoIndex, rel: str, depth: int = 0,
                       seen: frozenset[str] | None = None) -> set[str]:
    """Names a module file provides: defs, assignments, imports, plus one
    star-import hop (`from .models import *` re-exports everything). Depth
    capped with a seen-set; __init__ chains resolve in one hop in practice.
    """
    seen = seen or frozenset()
    if rel in seen or depth > 2:
        return set()
    seen = seen | {rel}
    out = set(index.file_symbols.get(rel, set())) | set(index.file_imports.get(rel, set()))
    for module, level in index.file_stars.get(rel, []):
        targets = _resolve_py_target(index, rel, module, level)
        if targets is None:
            continue
        for t in targets:
            if t in index.rel_paths:
                out |= _effective_symbols(index, t, depth + 1, seen)
    return out


def check_stale_import(facts: FileFacts, index: RepoIndex) -> list[Finding]:
    """Resolvable-import verification (Python; JS/TS relative imports).

    Python `from M import N` / JS `import {N} from './x'`: the module must
    exist, and N must be defined there, re-exported there, or itself a
    submodule. Guarded/conditional imports, bare specifiers (node_modules),
    stdlib, and deps are never flagged. Broken imports fail at load, so
    these are lies. See _check_stale_js_import for the JS/TS rules."""
    if facts.language == "javascript":
        return check_stale_js_import(facts, index)
    if facts.language != "python":
        return []
    findings: list[Finding] = []
    for module, level, names, is_guarded, lineno in facts.from_imports:
        if is_guarded:
            continue
        targets = _resolve_py_target(index, facts.path, module, level)
        if targets is None:
            continue
        existing = [t for t in targets if t in index.rel_paths]
        for name, _asname in names:
            if name == "*":
                continue
            if name.startswith("__") and name.endswith("__"):
                continue  # import system provides dunders (__file__, ...)
            if module is None:
                # `from . import sub`: the name may be a submodule file, a
                # name defined/re-exported by the package __init__ (stars
                # followed), or injected dynamically (`globals().update()`
                # in __init__: unknowable, never flagged — seen: CPython's
                # multiprocessing).
                base = "/".join(_resolve_py_base(facts.path, level))
                pkg_init = (base + "/__init__.py") if base else "__init__.py"
                if (name in index.file_symbols.get(pkg_init, set())
                        or name in index.file_imports.get(pkg_init, set())
                        or name in _effective_symbols(index, pkg_init)):
                    continue
                if _dynamic_ns(index, pkg_init) or not index.knows_symbols(pkg_init):
                    continue
                subs = ([base + "/" + name + ".py", base + "/" + name + "/__init__.py"]
                        if base else [name + ".py", name + "/__init__.py"])
                if any(s in index.rel_paths for s in subs):
                    continue
                findings.append(Finding(
                    path=facts.path, line=lineno, end_line=lineno,
                    checker="stale-import", severity="lie",
                    title=f"`from . import {name}` but no such submodule exists",
                    claim=f"from {'.' * level} import {name}",
                    evidence="No matching submodule file exists in this repo.",
                    fix="Fix the submodule path or remove the import.",
                    confidence=0.85,
                ))
                break
            if not existing:
                if module and not level and _shadowed_external(index, module):
                    break  # `pylint/` container dir vs the installed pylint
                if module and any(len(seg) > 1 and seg.isupper() for seg in module.split(".")):
                    break  # `components.NEW_DOMAIN`: a template placeholder
                if _registered_at_runtime(index, targets):
                    break  # an ancestor module fills sys.modules: unknowable
                if _py_target_has_stub(index, targets):
                    break  # compiled extension: the .pyi is the evidence
                if any(index.is_gitignored(t) for t in targets):
                    break  # build artifact (setuptools-scm _version.py)
                findings.append(Finding(
                    path=facts.path, line=lineno, end_line=lineno,
                    checker="stale-import", severity="lie",
                    title=f"`from {module}` imports from a module that does not exist",
                    claim=f"from {'.' * level}{module or ''} import {name}",
                    evidence="No such module file exists in this repo.",
                    fix="Fix the module path or remove the import.",
                    confidence=0.85,
                ))
                break
            if any(not index.knows_symbols(t) for t in existing):
                continue  # unparsed target may provide it: unknowable
            provided = any(name in _effective_symbols(index, t) for t in existing)
            # PEP 562: a module-level __getattr__ means any name may resolve.
            dynamic = any("__getattr__" in index.file_symbols.get(t, set()) for t in existing)
            # Dynamic namespace injection (`globals().update(...)`):
            # names cannot be enumerated statically. Seen: re/_constants.
            dynamic = dynamic or any(t in index.file_dynamic_ns for t in existing)
            # A module that replaces itself in sys.modules serves names from
            # the replacement (transformers/diffusers `_LazyModule`, whose
            # export list is generated from submodules): unknowable.
            dynamic = dynamic or any(t in index.file_replaces_self for t in existing)
            home = existing[0].rsplit("/", 1)[0] if "/" in existing[0] else ""
            submod = (home + "/" + name + ".py") if home else (name + ".py")
            subpkg = (home + "/" + name + "/__init__.py") if home else (name + "/__init__.py")
            if provided or dynamic or submod in index.rel_paths or subpkg in index.rel_paths:
                continue
            if any(_lazy_string_export(index, t, name) for t in existing):
                continue
            findings.append(Finding(
                path=facts.path, line=lineno, end_line=lineno,
                checker="stale-import", severity="lie",
                title=f"`{name}` imported from `{existing[0]}` but never defined there",
                claim=f"from {'.' * level}{module or ''} import {name}",
                evidence=f"`{existing[0]}` exists but defines no `{name}`.",
                fix=f"Check for a rename in `{existing[0]}` (or a moved submodule).",
                confidence=0.8,
            ))
    return _dedupe(findings)


# Output directories of code generators, each seen importing-before-codegen
# in a real repo (never a blanket dot-dir rule: `.github` is not generated).
#   styled-system  Panda CSS            (next.js examples/panda-css)
#   .mesh          GraphQL Mesh         (next.js examples/with-graphql-gateway)
#   edgeql-js      EdgeDB query builder (next.js examples/with-edgedb)
#   .source        fumadocs             (roadmap residual)
_CODEGEN_OUTPUT = re.compile(r"/(styled-system|\.mesh|edgeql-js|\.source)/")


def _js_exists_on_disk(index: RepoIndex, base: str, kind: str) -> bool:
    """Existence by Node's own rules, checked on disk: the index holds only
    scanned sources. CommonJS `require` appends extensions even after one
    (`require('./test.tsx')` loads `test.tsx.js`); JSON and native addons
    are loadable too."""
    root = index.root
    exts = ("",) + _JS_EXTS + (".json", ".node")
    try:
        for e in exts:
            if (root / (base + e)).is_file():
                return kind == "require" or e == "" or e in _JS_EXTS
        # Directory import: `../build` -> build/index.ts. Needed on disk
        # because directories named like build output are never indexed
        # (seen: next.js packages/next/src/build/).
        if (root / base).is_dir():
            return any((root / base / ("index" + e)).is_file() for e in _JS_EXTS)
        return False
    except OSError:
        return False


def _pkg_browser_field(index: RepoIndex, claimer: str) -> bool:
    """Whether the claimer's nearest package.json declares a `browser`
    object map (relative specifiers are rewritten by the bundler)."""
    import json as _json
    cur = posixpath.dirname(claimer)
    while True:
        pkg = index.root / cur / "package.json" if cur else index.root / "package.json"
        if pkg.is_file():
            try:
                data = _json.loads(pkg.read_text(encoding="utf-8", errors="ignore"))
            except (OSError, ValueError):
                return False
            return isinstance(data, dict) and isinstance(data.get("browser"), dict)
        if not cur:
            return False
        cur = posixpath.dirname(cur)


def _shadowed_external(index: RepoIndex, module: str) -> bool:
    """A missing absolute import whose top segment is a repo directory that
    is not a Python package (no `__init__.py`) names the installed package
    of the same name: home-assistant keeps its own lint plugins under a
    top-level `pylint/` folder, so `from pylint.checkers import
    BaseChecker` read as 161 missing modules. Namespace packages whose
    targets exist never reach this (they resolve first)."""
    top = module.split(".")[0]
    try:
        d = index.root / top
        return d.is_dir() and not (d / "__init__.py").is_file()
    except OSError:
        return False


def _py_target_has_stub(index: RepoIndex, targets: list[str]) -> bool:
    for t in targets:
        stub = t[:-3] + ".pyi"
        if stub in index.decl_paths:
            return True
    return False


def _lazy_string_export(index: RepoIndex, target: str, name: str) -> bool:
    """A name missing from a module's bindings but spelled as a quoted
    string literal there is almost always a lazy export: celery's
    `recreate_module` map, transformers' `_import_structure`, lazy_loader
    stubs. Suppression-only (measured: celery `Signature`)."""
    text = index.text_of(target)
    if not text:
        return False
    return re.search(r"""['"]""" + re.escape(name) + r"""['"]""", text) is not None


def _registered_at_runtime(index: RepoIndex, targets: list[str]) -> bool:
    """Whether an ancestor module of a missing import target writes
    `sys.modules`, which makes dotted paths below it importable with no
    file behind them (requests.packages -> urllib3)."""
    for t in targets:
        stem = t[:-len("/__init__.py")] if t.endswith("/__init__.py") else t[:-3]
        parts = stem.split("/")
        for i in range(len(parts) - 1, 0, -1):
            anc = "/".join(parts[:i])
            if (anc + ".py" in index.file_registers_modules
                    or anc + "/__init__.py" in index.file_registers_modules):
                return True
    return False


def _resolve_py_base(claimer: str, level: int) -> list[str]:
    parts = claimer.split("/")[:-1]
    if level - 1 > len(parts):
        return []
    return parts[:len(parts) - (level - 1)]


_JS_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts")

# TypeScript module resolution: a relative import written with a JS
# extension (`./suite.js`) resolves the same-named TS file (`suite.ts`)
# when no JS file exists. This is the default emitted by `tsc --init`
# ("Allow importing TS files with .js extensions") and is universal in
# TS test suites and ESM packages compiled from TS (seen: svelte's test
# suites import `../suite.js`, `./shared.js` with only .ts files on
# disk; OmniRoute's tests import `schemas.js` -> schemas.ts).
_JS_TS_SIBLINGS = {
    ".js": (".ts", ".tsx"),
    ".mjs": (".mts",),
    ".cjs": (".cts",),
    ".jsx": (".tsx",),
}


def _js_self_names(index: RepoIndex, claimer: str) -> set[str]:
    """Package names the claimer's own manifests say it ships (normalized).

    Walks every package.json from the scan root down to the claimer's
    directory (monorepo-aware: nearer manifests shadow nothing — a file
    may import any workspace's self-name). A repo that names itself
    (`"name": "svelte"`) resolves those imports through its exports map,
    bundler self-reference, or workspaces — none visible to snapshot
    analysis, so self-name bare imports are outside the decidable set.
    """
    rel = claimer.replace("\\", "/")
    parts = rel.split("/")[:-1]
    names: set[str] = set()
    seen: set[str] = set()
    for i in range(len(parts) + 1):
        d = "/".join(parts[:i]) if i else ""
        if d in seen:
            continue
        seen.add(d)
        try:
            data = json.loads((index.root / d / "package.json").read_text(
                encoding="utf-8", errors="ignore"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        name = data.get("name")
        if isinstance(name, str) and name:
            names.add(_norm_dist(name))
    return names


def _alias_prefix_match(spec: str, prefix: str) -> bool:
    """TypeScript `paths` semantics for a prefix (pattern minus `*`).

    A bare pattern (`preact`) matches only the exact module name; a
    trailing-slash pattern (`preact/`, `@/`) matches the subtree. Raw
    startswith hijacked sibling packages (`preact` mapping swallowed
    `preact-router` — measured: preact's demo drifted on a declared
    dependency).
    """
    return (spec == prefix or spec.startswith(prefix + "/")
            or (prefix.endswith("/") and spec.startswith(prefix)))


def _js_bare_externally_resolved(index: RepoIndex, claimer: str, spec: str) -> bool:
    """True when `spec` is a bare specifier whose resolution lies outside
    the snapshot, so stale-import must stay silent.

    A tsconfig/manual alias that maps a bare prefix onto the scanned tree
    is decidable and stays checkable (that is the alias feature). But when
    every replacement for the matched prefix resolves outside the tree,
    or is a types-only `.d.ts` surface that the scan deliberately excludes,
    the import is not falsifiable from a snapshot — most commonly the
    package's own name mapped to its type declarations (seen: svelte maps
    `svelte/*` -> `./src/*.d.ts`, which 1235 self-name imports then hit as
    drift), and node_modules-style mappings. Without node_modules there is
    no way to distinguish a lie from a resolution the scan cannot see.
    """
    for zone_dir, mapping in index.alias_zones:
        if zone_dir and not (claimer == zone_dir or claimer.startswith(zone_dir + "/")):
            continue
        for prefix, repls in mapping:
            if not _alias_prefix_match(spec, prefix):
                continue
            for repl in repls:
                if "node_modules" in repl.split("/"):
                    continue
                base = posixpath.normpath(
                    posixpath.join(zone_dir, repl, spec[len(prefix):])) if zone_dir \
                    else posixpath.normpath(repl + spec[len(prefix):])
                raw = _js_candidates_raw(base)
                if any(c in index.rel_paths for c in raw) or any(
                        not c.endswith(".d.ts") for c in raw):
                    return False
            return True
    return True


def _js_candidates_raw(base: str) -> list[str]:
    """Candidate paths without existence filtering (d.ts visibility)."""
    cands = ([base] if posixpath.splitext(base)[1].lower() in _JS_EXTS else []
             + [base + e for e in _JS_EXTS] + [base + "/index" + e for e in _JS_EXTS])
    stem, ext = posixpath.splitext(base)
    for sibling in _JS_TS_SIBLINGS.get(ext.lower(), ()):
        cands.append(stem + sibling)
    return [(c[2:] if c.startswith("./") else c) for c in cands]


def _file_tsconfig_excluded(index: RepoIndex, claimer: str) -> bool:
    """True when the claimer sits under a tsconfig `exclude` prefix."""
    rel = claimer.replace("\\", "/")
    return any(rel == p or rel.startswith(p + "/") for p in index.tsconfig_excluded)


def _base_in_ignored_dir(base: str) -> bool:
    """True when a candidate module path sits under a directory suffix the
    scan deliberately ignores (vendor, build, dist, coverage, ...). Shared
    by the relative and alias resolution arms: such files are never
    indexed, so their existence cannot be judged and "does not exist" is
    never positive evidence.
    """
    for part in base.split("/")[:-1]:
        if part in IGNORED_DIR_NAMES:
            return True
    return False


def _target_escapes_root(base: str) -> bool:
    """True when a resolved candidate path leaves the scanned tree.

    A specifier that walks above the scan root resolves against files the
    snapshot never indexed (a monorepo's sibling package, the repo root's
    own trees). Their existence cannot be judged from the snapshot, so
    reporting "does not exist" is never positive evidence — the same
    verdict as a scan-ignored directory. Mechanical and suppression-only:
    it can only remove findings, never invent one.

    Measured (OmniRoute, 2026-09-22): scanning `src/` reported 51
    stale-import lies for `../../shared/...` and `../../../open-sse/...`
    specifiers whose targets exist one level above the root; the same
    tree scanned from the repo root reported 3. Sub-tree and single-file
    scans are first-class agent workflows, so a partial snapshot must
    never manufacture absence claims about what it did not look at.
    """
    return base == ".." or base.startswith("../")


def _js_target_in_ignored_dir(index: RepoIndex, claimer: str, spec: str) -> bool:
    """True when a (relative) specifier's candidate targets sit under a
    directory suffix the scan deliberately ignores (vendor, build, dist,
    docs, coverage, ...). Their existence cannot be judged from the
    snapshot: they may or may not be real files, so "does not exist" is
    never positive evidence.
    """
    return _base_in_ignored_dir(posixpath.normpath(
        posixpath.join(posixpath.dirname(claimer), spec)))


def _js_dir_main_target(index: RepoIndex, base: str) -> list[str] | None:
    """Resolve a directory specifier through its package.json entry point.

    `import { h } from '../../'` in preact's own tests names the package
    root: Node resolves it via `main`/`module`/`exports["."]`. When that
    entry exists in-tree it becomes the checkable target (existence AND
    named bindings verify against it); when it points at build output
    (`dist/`, absent pre-build) or is missing, the import is
    build-dependent and unjudgeable — silent, never a lie. A directory
    with no package.json entry keeps the old verdict (index files or
    missing). Measured: preact's `../../` test imports drifted on a
    `dist/` main.
    """
    if base == ".":
        base = ""  # normpath spells the scan root "." rather than ""
    if base and base not in index.dirs:
        return []
    try:
        data = json.loads((index.root / base / "package.json").read_text(
            encoding="utf-8", errors="ignore"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    entry: str | None = None
    exports = data.get("exports")
    if isinstance(exports, dict):
        dot = exports.get(".")
        if isinstance(dot, str):
            entry = dot
        elif isinstance(dot, dict):
            for cond in ("import", "require", "default", "module"):
                cand = dot.get(cond)
                if isinstance(cand, str):
                    entry = cand
                    break
    if entry is None:
        for key in ("main", "module"):
            cand = data.get(key)
            if isinstance(cand, str):
                entry = cand
                break
    if not entry:
        return []
    target = posixpath.normpath(posixpath.join(base, entry))
    if _base_in_ignored_dir(target) or _target_escapes_root(target):
        return None  # build output or outside the snapshot: unjudgeable
    if target in index.rel_paths:
        return [target]
    # Entry point genuinely absent and not build output: stale-entrypoint
    # owns the package.json verdict; the import stays silent here rather
    # than double-reporting a manifest typo as an import lie.
    return None


def _resolve_js_target(index: RepoIndex, claimer: str, spec: str) -> list[str] | None:
    """Candidate module rel paths for a JS/TS specifier.

    Relative (./, ../) resolves directly. Alias prefixes (@/, ~/ and
    tsconfig/manual mappings) resolve through the longest matching zone;
    a matched-but-unresolvable alias is a missing module (drift), while a
    specifier matching nothing stays silent. Bare imports live in
    node_modules: outside snapshot analysis, always silent. Returns None
    (skip) or a possibly-empty list (empty = module does not exist).
    """
    if spec.startswith("./") or spec.startswith("../"):
        base = posixpath.normpath(
            posixpath.join(posixpath.dirname(claimer), spec))
        if _target_escapes_root(base):
            return None  # above the scan root: outside the snapshot
        found = _js_candidates(index, base)
        if not found and not posixpath.splitext(base)[1]:
            # Directory specifier with no index file: resolve through
            # the package entry point (or stay silent when build-made).
            return _js_dir_main_target(index, base)
        return found
    for zone_dir, mapping in index.alias_zones:
        if zone_dir and not (claimer == zone_dir or claimer.startswith(zone_dir + "/")):
            continue
        for prefix, repls in mapping:
            if not _alias_prefix_match(spec, prefix):
                continue
            rest = spec[len(prefix):]
            found: list[str] = []
            external_only = True
            for repl in repls:
                if "node_modules" in repl.split("/"):
                    continue  # types-only or dep mappings: outside the snapshot
                external_only = False
                base = posixpath.normpath(posixpath.join(zone_dir, repl, rest)) if zone_dir else posixpath.normpath(repl + rest)
                if _base_in_ignored_dir(base) or _target_escapes_root(base):
                    # Alias mapped into a scan-ignored dir (seen: OmniRoute
                    # `@omniroute/open-sse/*` reaching tracked vendor code):
                    # existence is unknowable from the snapshot — never a
                    # finding, same verdict as the relative arm.
                    return None
                found.extend(_js_candidates(index, base))
            if found:
                return found
            if external_only:
                continue  # mapping declares externality: like a bare specifier
            return []
    return None


def _js_candidates(index: RepoIndex, base: str) -> list[str]:
    cands = ([base] if posixpath.splitext(base)[1].lower() in _JS_EXTS else []
             + [base + e for e in _JS_EXTS] + [base + "/index" + e for e in _JS_EXTS])
    # TS-style JS-extension imports: `./x.js` also resolves `./x.ts` when
    # no JS file exists (see _JS_TS_SIBLINGS).
    stem, ext = posixpath.splitext(base)
    for sibling in _JS_TS_SIBLINGS.get(ext.lower(), ()):
        cands.append(stem + sibling)
    # rel_paths stores both `x` and `./x`; every other map (exports,
    # imports, symbols) uses the clean form, so normalize: a `./`-form
    # target otherwise misses every export lookup (seen: express examples).
    return [(c[2:] if c.startswith("./") else c) for c in cands if c in index.rel_paths]


def _effective_js_exports(index: RepoIndex, rel: str, depth: int = 0,
                          seen: frozenset[str] | None = None) -> tuple[set[str], bool]:
    """(exported names, complete?). Bare `export *` makes the set
    unknowable: callers must stay silent rather than guess."""
    seen = seen or frozenset()
    if rel in seen or depth > 2:
        return set(), True
    seen = seen | {rel}
    out = set(index.file_exports.get(rel, set()))
    complete = rel not in index.file_export_unknown
    for spec in index.file_export_stars.get(rel, []):
        targets = _resolve_js_target(index, rel, spec)
        if targets is None:
            complete = False
            continue
        for t in targets:
            names, ok = _effective_js_exports(index, t, depth + 1, seen)
            out |= names
            complete = complete and ok
    return out, complete


def check_stale_js_import(facts: FileFacts, index: RepoIndex) -> list[Finding]:
    """JS/TS relative imports: module must exist; named bindings must be
    exported (star re-exports followed); defaults need a default export."""
    if facts.language != "javascript":
        return []
    # Import statements inside comments are prose, not imports. Line-based
    # extraction cannot know that, so comment-only lines are excluded here.
    comment_lines: set[int] = set()
    for c in facts.comments:
        for ln in range(c.line, c.end_line + 1):
            comment_lines.add(ln)
    self_names = _js_self_names(index, facts.path)
    findings: list[Finding] = []
    for spec, kind, default, named, lineno in facts.js_imports:
        if lineno in comment_lines:
            continue
        if spec.startswith((".", "/")) and ("?" in spec or "#" in spec):
            # Bundler resource queries (`./worker?worker`, `./a.svg?url`,
            # `./x.js#hash`): the file is the part before the query, and the
            # bindings are whatever the loader synthesizes (a Worker
            # constructor, a URL string), so only existence is checkable.
            # Seen: 16 vite playground imports reported missing.
            spec = re.split(r"[?#]", spec, maxsplit=1)[0]
            default, named = None, []
            if not spec or spec in (".", "./", "/"):
                continue
        ext = posixpath.splitext(spec)[1].lower()
        if ext and ext not in _JS_EXTS:
            continue  # asset imports (css, json, svg): bundler surface
        if not spec.startswith(("./", "../", "/")):
            # Bare specifier: the default case is a node_modules package —
            # outside snapshot analysis, always silent. Two decidable
            # exceptions remain: an alias mapping onto the scanned tree,
            # and the repo's own package name (self-import). Both are
            # suppressed here when their resolution is not visible to the
            # snapshot (types-only targets, external mappings); only the
            # in-tree alias case continues to verdicts below.
            pkg = "/".join(spec.split("/")[:2]) if spec.startswith("@") else spec.split("/")[0]
            if _norm_dist(pkg) in self_names or _js_bare_externally_resolved(
                    index, facts.path, spec):
                continue
        targets = _resolve_js_target(index, facts.path, spec)
        if targets is None:
            continue
        if not targets:
            # Ambient type surface: an extensionless specifier whose exact
            # .d.ts target exists (`./types` -> types.d.ts, seen: svelte's
            # internal client tests) resolves in TypeScript's ambient space.
            # Declaration files are never parsed as source, only tracked
            # (index.decl_paths), so this silence is evidence-based.
            if not posixpath.splitext(spec)[1]:
                base = posixpath.normpath(
                    posixpath.join(posixpath.dirname(facts.path), spec))
                if base + ".d.ts" in index.decl_paths or base + "/index.d.ts" in index.decl_paths:
                    continue
            # The repo excludes the claimer from its own typecheck
            # (tsconfig `exclude`): templates/scaffolds whose relative
            # imports resolve only after codegen transplantation (seen:
            # svelte's excluded scripts/process-messages/templates/).
            # Not a checkable claim — the file is outside the contract.
            if _file_tsconfig_excluded(index, facts.path):
                continue
            base = posixpath.normpath(posixpath.join(posixpath.dirname(facts.path), spec))
            if index.is_gitignored(base) or any(
                    index.is_gitignored(base + e) for e in _JS_EXTS):
                continue  # gitignored build/test output
            if _CODEGEN_OUTPUT.search("/" + base + "/"):
                continue  # written by a code generator after install
            if spec.startswith((".", "/")) and _js_exists_on_disk(index, base, kind):
                continue  # present but unindexed (CJS `.tsx` + `.js`, json)
            if _pkg_browser_field(index, facts.path):
                # package.json `browser` object remaps relative specifiers
                # at bundle time (seen: vite playground/resolve/browser-field).
                continue
            # Relative misses are lies (relative paths are always local).
            # Alias misses are drift: the target may be generated at build
            # time (registry outputs) or live outside the scanned tree.
            # Either way they never fail a default gate.
            is_relative = spec.startswith("./") or spec.startswith("../")
            # ...unless the target sits in a directory the scan deliberately
            # ignores (vendor trees, build dirs, dist): then "does not exist"
            # is knowingly wrong — the file is there, the scan just cannot
            # index it (seen: OmniRoute's tracked open-sse/vendor/ and
            # scripts/build/). The .d.ts exception keeps the one case where
            # the checker has positive evidence of a shimmed lie.
            if is_relative and _js_target_in_ignored_dir(index, facts.path, spec) \
                    and not posixpath.splitext(spec)[1].lower() == ".d.ts" \
                    and not spec.endswith((".d.ts", ".d.mts", ".d.cts")):
                continue
            findings.append(Finding(
                path=facts.path, line=lineno, end_line=lineno,
                checker="stale-import",
                severity="lie" if is_relative else "drift",
                title=f"imports from `{spec}`, which does not exist",
                claim=spec,
                evidence="No such module file exists in this repo.",
                fix="Fix the specifier or remove the import.",
                confidence=0.85 if is_relative else 0.6,
            ))
            continue
        if kind in ("sideeffect", "namespace"):
            continue
        provided: set[str] = set()
        complete = True
        for t in targets:
            names, ok = _effective_js_exports(index, t)
            provided |= names
            complete = complete and ok
        if not complete:
            continue  # unknowable export surface: stay silent, never guess
        if not provided and all(t not in index.file_esm for t in targets):
            # A target that exports nothing at all is a build-time shim
            # replaced by the bundler (React's ReactFiberConfig.js throws
            # "This module must be shimmed"), not a module that lost names.
            continue
        if default is not None and "default" not in provided:
            if not all(t in index.file_esm for t in targets):
                continue  # CJS/script target: default interop always binds
            if kind == "require" and not facts.path.endswith(
                    (".mts", ".cts", ".mjs", ".cjs")):
                # require() default-shape from a .js file: the require()d
                # module is read at runtime; when the importer itself is
                # not forcibly-ESM (.mjs), the file may run as CJS where
                # `const mod = require('x')` binds any module.exports shape
                # (seen: electron/loginManager.js requiring a TS-compiled
                # service with only named exports). Not falsifiable from a
                # snapshot.
                continue
            findings.append(Finding(
                path=facts.path, line=lineno, end_line=lineno,
                checker="stale-import", severity="lie",
                title=f"default import `{default}` from `{spec}`, which has no default export",
                claim=default,
                evidence=f"`{targets[0]}` exists but exposes no default export.",
                fix=f"Check for a rename in `{targets[0]}` or import a named binding.",
                confidence=0.75,
            ))
        for original, alias in named:
            if kind == "require" and not provided:
                continue  # CJS without visible exports: cannot decide
            if original not in provided:
                findings.append(Finding(
                    path=facts.path, line=lineno, end_line=lineno,
                    checker="stale-import", severity="lie",
                    title=f"`{original}` imported from `{spec}` but never exported there",
                    claim=original if original == alias else f"{original} as {alias}",
                    evidence=f"`{targets[0]}` exists but exports no `{original}`.",
                    fix=f"Check for a rename in `{targets[0]}`.",
                    confidence=0.8,
                ))
    return _dedupe(findings)
