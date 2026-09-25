"""stale-contract-ref (experimental): deprecation, lock and env-default claims.
"""
from __future__ import annotations

import re

from ..models import FileFacts, Finding
from ..repo_index import RepoIndex
from ._shared import (
    _STDLIB_MODULES,
    _TICKET,
    _appears_in_code,
    _code_text,
    _dedupe,
    _is_dunder,
    _is_reserved,
    _stdlib_class,
    _suggest,
)


# ---------------------------------------------------------------- stale-contract-ref
# EXPERIMENTAL, opt-in only. Architectural claims in comments that rot
# silently: deprecation targets, lock-holder requirements, env defaults.
# Narrow trigger frames only; backticked names stay with stale-symbol-ref
# (no double-reporting), ticket/URL-anchored claims point outside the
# snapshot on purpose (v2 rule) and are skipped.

_CONTRACT_DEPRECATION = re.compile(
    r"deprecat\w*|\breplac\w*\s+by\b|\bsupersed\w*\s+by\b"
    r"|\bmigrat\w+\s+to\b|\buse\b[^.\n]{0,80}?\binstead\b",
    re.IGNORECASE,
)
_CONTRACT_NAME = re.compile(
    r"(?<![A-Za-z0-9_$.`\"'])([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+"
    r"|[A-Za-z_][A-Za-z0-9_]*\([^()\n]*\))"
)
_CONTRACT_LOCK = re.compile(
    r"(?:must\s+hold|holding|guarded\s+by|protected\s+by|requires?(?:\s+holding)?"
    r"|acquires?|takes?)\s+`?(_?[A-Za-z][\w.]*)`?",
    re.IGNORECASE,
)
_CONTRACT_LOCK_LIKE = re.compile(r"^_|lock|mutex|guard|monitor|semaphore", re.IGNORECASE)
_CONTRACT_ENV_PY = re.compile(
    r"os\.getenv\(\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']\s*,\s*([^,)\n]+)\)"
    r"|os\.environ\.get\(\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']\s*,\s*([^,)\n]+)\)"
)
_CONTRACT_ENV_JS = re.compile(
    r"process\.env\.([A-Za-z_][A-Za-z0-9_]*)\s*(?:\?\?|\|\|)\s*([^,;)\n]+)"
)
_CONTRACT_ENV_JS_DESTRUCT = re.compile(r"\{\s*([^}]*)\}\s*=\s*process\.env\b")
_CONTRACT_DEFAULT_FRAME = re.compile(
    r"\bdefault(?:s)?(?:\s+(?:is|to|port|of))?\b|\bfallback\b|\bunless\s+set\b|\boverride\b",
    re.IGNORECASE,
)
_CONTRACT_LITERAL = re.compile(r"(\d+(?:\.\d+)?|[\"'][^\"']+[\"'])")


def _contract_norm_literal(raw: str) -> str:
    s = raw.strip().rstrip(",;")
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def _contract_known(root: str, base: str, facts: FileFacts, index: RepoIndex,
                    code: str, language: str) -> bool:
    if root in facts.imports:
        return True
    if language == "python" and (root in _STDLIB_MODULES or _stdlib_class(root)):
        return True
    if _appears_in_code(root, code) or (root != base and _appears_in_code(base, code)):
        return True
    return bool(index.has_symbol(root) or index.has_symbol(base))


def _loose_dist(name: str) -> str:
    """alphanumeric-only lowercase: bodyParser == body-parser."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def check_stale_contract_ref(facts: FileFacts, index: RepoIndex) -> list[Finding]:
    """Deprecation targets, lock holders, and env defaults that lie.

    Only narrow frames: a deprecation sentence naming a replacement, a
    lock-requirement naming the lock, a same-file comment stating a
    default for an env var read with a different default in code.
    Bare prose never reports; only contradictions do.
    """
    findings: list[Finding] = []
    code = _code_text(facts)
    language = facts.language if facts.language in ("python", "javascript", "go", "c") else "python"
    for c in facts.comments:
        text = c.text
        if not text.strip() or _TICKET.search(text):
            continue
        if _CONTRACT_DEPRECATION.search(text):
            strong = bool(re.search(
                r"deprecat\w*|\breplac\w*\s+by\b|\bsupersed\w*\s+by\b|\bmigrat\w+\s+to\b",
                text, re.IGNORECASE))
            for m in _CONTRACT_NAME.finditer(text):
                raw = m.group(1).rstrip(".")
                is_call = raw.endswith(")")
                name = raw[:raw.index("(")] if is_call else raw
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", name or ""):
                    continue
                base = name.split(".")[-1]
                root = name.split(".")[0]
                if len(base) < 3 or _is_dunder(base) or _is_reserved(base, language):
                    continue
                if not strong and base.isupper():
                    continue  # bare "use X instead" naming SQL/platform
                    # builtins (JSON_TYPE, COALESCE): uppercase convention
                if "." not in name and not is_call:
                    continue  # bare word in prose, not a reference
                if _contract_known(root, base, facts, index, code, language):
                    continue
                hint = _suggest(base, index)
                findings.append(Finding(
                    path=facts.path, line=c.line, end_line=c.end_line,
                    checker="stale-contract-ref", severity="lie",
                    title=f"Deprecation notice points at `{name}` which does not exist",
                    claim=f"`{name}`",
                    evidence=f"`{root}` is not defined, imported, or used in this file, "
                             f"and no definition of `{base}` was found in {len(index.files)} indexed source files.",
                    fix=f"Point the notice at the real replacement, or remove it.{hint}",
                    confidence=0.8,
                ))
        for m in _CONTRACT_LOCK.finditer(text):
            name = m.group(1).rstrip(".")
            base = name.split(".")[-1]
            root = name.split(".")[0]
            if len(base) < 3 or _is_dunder(base) or _is_reserved(base, language):
                continue
            if not _CONTRACT_LOCK_LIKE.search(base):
                continue  # "must hold a reference": prose, not a lock
            if _contract_known(root, base, facts, index, code, language):
                continue
            findings.append(Finding(
                path=facts.path, line=c.line, end_line=c.end_line,
                checker="stale-contract-ref", severity="lie",
                title=f"Threading claim requires `{name}` which does not exist",
                claim=f"`{name}`",
                evidence=f"No `{base}` is defined, imported, or used in this file, "
                         f"and none was found in {len(index.files)} indexed source files: "
                         f"the lock was likely renamed.",
                fix="Update the claim to the current lock name.",
                confidence=0.8,
            ))
    if facts.language in ("python", "javascript"):
        skip: set[int] = set()
        for c in facts.comments:
            for ln in range(c.line, c.end_line + 1):
                skip.add(ln)
        for idx, line in enumerate(facts.lines, start=1):
            if idx in skip or not line.strip():
                continue
            pairs: list[tuple[str, str]] = []
            if facts.language == "python":
                for m in _CONTRACT_ENV_PY.finditer(line):
                    var = m.group(1) or m.group(3)
                    default = m.group(2) if m.group(1) else m.group(4)
                    if var and default is not None:
                        pairs.append((var, _contract_norm_literal(default)))
            else:
                for m in _CONTRACT_ENV_JS.finditer(line):
                    pairs.append((m.group(1), _contract_norm_literal(m.group(2))))
                for dm in _CONTRACT_ENV_JS_DESTRUCT.finditer(line):
                    for part in dm.group(1).split(","):
                        if "=" in part:
                            k, _, v = part.partition("=")
                            k = k.strip()
                            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
                                pairs.append((k, _contract_norm_literal(v)))
            for var, default in pairs:
                for c in facts.comments:
                    if not re.search(r"\b" + re.escape(var) + r"\b", c.text):
                        continue
                    if not _CONTRACT_DEFAULT_FRAME.search(c.text):
                        continue
                    found = False
                    for lm in _CONTRACT_LITERAL.finditer(c.text):
                        claimed = _contract_norm_literal(lm.group(1))
                        if claimed != default:
                            findings.append(Finding(
                                path=facts.path, line=c.line, end_line=c.end_line,
                                checker="stale-contract-ref", severity="drift",
                                title=f"Comment claims default `{claimed}` for `{var}`, code uses `{default}`",
                                claim=f"{var} default {claimed}",
                                evidence=f"`{var}` is read with default `{default}` in this file.",
                                fix=f"Update the comment to `{default}`, or change the code default.",
                                confidence=0.7,
                            ))
                            found = True
                            break
                    if found:
                        break
    return _dedupe(findings)
