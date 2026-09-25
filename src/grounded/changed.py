"""`scan --changed`: report what the change introduced, not every finding
that shares a word with it.

A finding is reported when it sits on a changed line or in a new file
(unchanged from before), or when the change introduced it anywhere else:
it is in the scan of the worktree and not in the scan of the base. The
base scan is cheap because it is narrow:

1. The base index is the current index with the changed files' entries
   replaced by the entries of their base text (RepoIndex.base_variant);
   no other file is indexed again.
2. A finding outside the changed files can only appear when an index
   fact it depends on changed, and it names that fact: the missing
   symbol, the module, the mocked path. So the candidates are the files
   whose text names something whose index entry changed.
3. Candidates and changed files are checked under both indexes;
   introduced = worktree findings minus base findings (by baseline
   fingerprint, counted, so a second copy of an old finding is new).

Some checkers read files the index does not hold (manifests, .gitignore,
package.json, tsconfig), and the disk only shows the worktree. When the
change edits one of those, the base scan cannot be reconstructed, so the
scan falls back to the conservative rule: every finding whose claim names
an identifier from the diff (a superset, noisier by design). The reason
is reported. Checkers also test whether a path exists on disk; for a
deleted path the base check cannot see the base's copy, so findings that
name a deleted path are reported without it (ChangedPlan.unverifiable).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .delta import collect_changes, fingerprint
from .models import Finding

# Files checkers read from disk rather than from the index.
_CONTEXT_NAMES = frozenset({
    "pyproject.toml", "package.json", "setup.py", "setup.cfg", ".gitignore",
    "grounded.toml", ".grounded.toml", "go.mod",
})

# Per-file index facts whose change can alter findings in other files
# beyond the names they hold: which module is opaque, star-exported, or
# registers modules at runtime. A change here widens the candidates to
# every name the file defines plus its module path.
_SURFACE_KEYS = frozenset({"parse_failed", "file_dynamic_ns", "file_registers_modules",
                           "file_replaces_self", "file_export_unknown", "file_esm",
                           "file_stars", "file_export_stars"})

_TOKEN = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
# Same floor as the diff-token rule this replaces: shorter tokens (`i`,
# `x`, `id`) name nothing specific and would make every file a candidate.
_MIN_TOKEN = 3


@dataclass
class ChangedPlan:
    root: Path
    ref: str
    hunks: dict[str, set[int]]
    untracked: set[str]
    symbols: set[str]
    status: dict[str, str]
    mode: str = "precise"
    reason: str = ""
    checked: int = 0
    introduced: int = 0
    changed_names: set[str] = field(default_factory=set)
    # Tokens of deleted paths. Checkers test existence on disk, where the
    # base's copy is gone, so a base check cannot vouch for findings about
    # them: findings naming them are reported as introduced.
    unverifiable: set[str] = field(default_factory=set)
    stats: dict = field(default_factory=dict)

    @classmethod
    def from_git(cls, root: Path, base: str) -> "ChangedPlan":
        ref, hunks, untracked, symbols, status = collect_changes(root, base)
        return cls(root=root, ref=ref, hunks=hunks, untracked=untracked,
                   symbols=symbols, status=status)

    def on_changed_line(self, f: Finding) -> bool:
        if f.path in self.untracked or self.status.get(f.path) == "A":
            return True
        lines = self.hunks.get(f.path)
        return bool(lines) and any(ln in lines for ln in range(f.line, f.end_line + 1))


def legacy_reason(status: dict[str, str]) -> str:
    """Why the base cannot be reconstructed from the index, or ""."""
    for rel, code in sorted(status.items()):
        name = rel.rsplit("/", 1)[-1]
        if name in _CONTEXT_NAMES or (name.startswith("requirements") and name.endswith(".txt")) \
                or (name.startswith(("tsconfig", "jsconfig")) and name.endswith(".json")):
            return f"{rel} configures how other files resolve"
    return ""


def _names(value) -> set[str]:
    out: set[str] = set()
    if value is True:
        return out
    for item in value:
        if isinstance(item, str):
            out.add(item)
        elif isinstance(item, tuple):
            out.update(x for x in item if isinstance(x, str))
    return out


def changed_names(old: dict | None, new: dict | None, rel: str,
                  ghost: bool) -> tuple[set[str], set[str], set[str]]:
    """What one file's index change can affect elsewhere, from its base
    and current entry: (names, module tokens, attribute names).

    - names: symbols whose presence in this file changed (defined,
      imported, exported). Callers split them by whether the change moved
      the name's repo-wide presence.
    - module tokens: when an opacity/star/runtime-registration fact
      changed, any name imported through this module may resolve
      differently, so every file naming the module is a candidate.
    - attribute names: attribute uses only ever suppress ghost-export,
      whose finding sits in the module defining the attribute.
    """
    old, new = old or {}, new or {}
    names: set[str] = set()
    module: set[str] = set()
    attrs: set[str] = set()
    for key in set(old) | set(new):
        a, b = old.get(key), new.get(key)
        if a == b:
            continue
        if key in _SURFACE_KEYS:
            module |= path_tokens(rel)
        elif key == "file_attr_uses":
            if ghost:
                attrs |= {attr for _, attr in (a or set()) ^ (b or set())}
        else:
            names |= _names(a or ()) ^ _names(b or ())
    return names, module, attrs


def path_tokens(rel: str) -> set[str]:
    """Tokens every reference to the module at rel contains: its stem, the
    last segment of any dotted or relative import (`http.server`,
    `from . import server`, `'./server'`), or for a package file
    (`__init__.py`, `index.js`) its directory's name. Other directories
    are left out on purpose: source roots and test dirs (`Lib`, `src`,
    `test`) appear in no import, and as tokens they made nearly every file
    a candidate (measured: 1,356 of 3,605 on cpython for one rename)."""
    parts = rel.split("/")
    stem = parts[-1].rsplit(".", 1)[0]
    if stem in ("__init__", "index") and len(parts) > 1:
        stem = parts[-2]
    return set(_TOKEN.findall(stem))


def search_tokens(names: set[str]) -> set[str]:
    """The tokens candidate_filter searches for `names` (same floor)."""
    return _tokens(names)


def _tokens(names: set[str]) -> set[str]:
    return {t for n in names for t in _TOKEN.findall(n) if len(t) >= _MIN_TOKEN}


def _alt(tokens) -> str:
    return "|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True))


def _dotted(text: str, name: str, mods: frozenset[str]) -> bool:
    """`mod.name` in text with mod in mods (mock targets like
    `patch('http.server.test')`, attribute uses, comments). A literal find
    for `.name` plus a look-back: an unanchored `(\\w+)\\.name` regex is
    tried at every position (measured 5.8 s over cpython for one name)."""
    needle = "." + name
    i = text.find(needle)
    n = len(text)
    while i != -1:
        end = i + len(needle)
        if end == n or not (text[end].isalnum() or text[end] in "_$"):
            j = i
            while j > 0 and (text[j - 1].isalnum() or text[j - 1] in "_$"):
                j -= 1
            if text[j:i] in mods:
                return True
        i = text.find(needle, i + 1)
    return False


def candidate_filter(anywhere: set[str], scoped: dict[str, set[str]],
                     anywhere_files: set[str] | None = None):
    """Predicate over (file text, the file's imported module tokens). A
    file is a candidate when it names a token from `anywhere` (a symbol
    whose repo-wide presence changed), or names a `scoped` symbol and
    reaches it through one of its modules: imports the module, or spells
    `module.name`. A symbol whose repo-wide presence did not change can
    only break through the module it lives in or one re-exporting it.

    `anywhere_files`, when given, is the precomputed set of files naming an
    `anywhere` token (see delta.grep_files); files outside it are not
    searched again."""
    any_tokens = _tokens(anywhere)
    any_rx = (re.compile(rf"(?<![A-Za-z0-9_$])(?:{_alt(any_tokens)})(?![A-Za-z0-9_$])")
              if any_tokens else None)
    # A few tokens: substring tests reject most files before the regex
    # (the lookbehind alternation scans every position of every file).
    any_literals = tuple(any_tokens) if len(any_tokens) <= 8 else ()
    checks = []
    for name, mods in sorted(scoped.items()):
        mods = frozenset(m for m in mods if m)
        if len(name) < _MIN_TOKEN or not mods:
            continue
        checks.append((name, mods, re.compile(
            rf"(?<![A-Za-z0-9_$]){re.escape(name)}(?![A-Za-z0-9_$])")))

    def hit(rel: str, text: str, imported: set[str]) -> bool:
        if anywhere_files is not None:
            if rel in anywhere_files:
                return True
        # Substring tests first (C speed); a regex only confirms whole words.
        elif any_rx is not None and (not any_literals or any(t in text for t in any_literals)) \
                and any_rx.search(text):
            return True
        for name, mods, name_rx in checks:
            if name not in text or not name_rx.search(text):
                continue
            if imported & mods or _dotted(text, name, mods):
                return True
        return False
    return hit


def claim_tokens(f: Finding) -> set[str]:
    return set(_TOKEN.findall(f.claim or "")) | set(_TOKEN.findall(f.title or ""))


def introduced(new: list[Finding], base: list[Finding]) -> list[Finding]:
    """Findings of `new` beyond the base's count of the same fingerprint."""
    budget = Counter(fingerprint(f) for f in base)
    out: list[Finding] = []
    for f in new:
        fp = fingerprint(f)
        if budget[fp] > 0:
            budget[fp] -= 1
        else:
            out.append(f)
    return out
