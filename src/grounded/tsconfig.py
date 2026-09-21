"""tsconfig path-alias resolution (stdlib only, JSONC tolerant).

Real tsconfig files contain comments, trailing commas are rare but
`extends` chains are common. This implements the subset that matters for
import checking: baseUrl-agnostic `paths` with longest-prefix matching
(the TypeScript rule), single-pattern or first-match lists, and one
`extends` chain. Anything fancier resolves to "unknown" (skip), never to
a guess: a wrong mapping manufactures false lies.
"""
from __future__ import annotations

import json
from pathlib import Path


def strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments outside string literals."""
    out: list[str] = []
    i, n = 0, len(text)
    in_str: str | None = None
    while i < n:
        ch = text[i]
        if in_str is not None:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ("\"", "'"):
            in_str = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def load_tsconfig(path: Path) -> dict:
    """Parsed compilerOptions with extends resolved (child wins)."""
    try:
        data = json.loads(strip_jsonc(path.read_text(encoding="utf-8", errors="ignore")))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    base: dict = {}
    extends = data.get("extends")
    if isinstance(extends, str):
        parent = (path.parent / extends)
        if not parent.suffix:
            parent = parent.with_suffix(".json")
        if parent.exists():
            base = load_tsconfig(parent)
    merged = dict(base)
    child_opts = data.get("compilerOptions")
    if isinstance(child_opts, dict):
        merged_opts = dict(base.get("compilerOptions", {}))
        merged_opts.update(child_opts)
        merged["compilerOptions"] = merged_opts
    return merged


def alias_map(opts: dict) -> list[tuple[str, list[str]]]:
    """Ordered (prefix, [replacement prefixes]) longest-prefix first."""
    paths = (opts.get("compilerOptions") or {}).get("paths") or {}
    if not isinstance(paths, dict):
        return []
    out: list[tuple[str, list[str]]] = []
    for pattern, targets in paths.items():
        if not isinstance(pattern, str) or not isinstance(targets, list):
            continue
        prefix = pattern.split("*")[0]
        repls = []
        for t in targets:
            if isinstance(t, str):
                repls.append(t.split("*")[0])
        if prefix and repls:
            out.append((prefix, repls))
    out.sort(key=lambda item: -len(item[0]))
    return out
