"""stale-symbol-ref: comments that name a call resolving nowhere.
"""
from __future__ import annotations

import re

from ..models import FileFacts, Finding
from ..repo_index import RepoIndex
from ._shared import (
    JS_GLOBALS,
    REFERENCE_VERBS,
    _BACKTICK_SYMBOL,
    _DUNDER_OK,
    _HISTORICAL,
    _METASYNTACTIC_CALLS,
    _NEGATED,
    _STDLIB_MODULES,
    _SYMBOL_CALL,
    _TICKET,
    _WORKITEM,
    _appears_as_suffix,
    _appears_in_code,
    _code_text,
    _comment_block_text,
    _commented_code_line_set,
    _dedupe,
    _dunder_typo_of,
    _external_or_family,
    _is_dunder,
    _is_reserved,
    _scrub_docstring,
    _stdlib_class,
    _suggest,
)


# ---------------------------------------------------------------- checkers

def _foreign_root(root: str, index: RepoIndex) -> bool:
    """A lowercase dotted claim is about the repo only when its root is
    something the repo owns: a symbol it defines, a module file or package it contains,
    or a top-level entry. `jax.numpy.select()` and `nn.utils.weight_norm()`
    in a comment name other libraries' APIs (seen: transformers, where
    `nn`/`jax` were not imported in the commenting file)."""
    if not root[:1].islower():
        # Class-like roots (a `TestCase` subclass and its method) are usually the repo's
        # own, renamed or deleted: keep judging them. Foreign namespaces
        # in comments are module aliases, lowercase by convention.
        return False
    if root in index.all_symbols or root in index.top_names or root in index.py_prefixes:
        return False
    stems = index.__dict__.get("_module_stems")
    if stems is None:
        stems = set()
        for rel in index.rel_paths:
            parts = rel.lstrip("./").split("/")
            stems.update(p.rsplit(".", 1)[0] for p in parts)
        index.__dict__["_module_stems"] = stems
    return root not in stems


def check_stale_symbol(facts: FileFacts, index: RepoIndex) -> list[Finding]:
    """Backticked calls, verb-anchored bare calls, and dunder typos only.

    Plain backticked words are usually Sphinx fields, attributes, options,
    or prose, so they are skipped (dunders excepted: a dunder with no
    definition anywhere is almost always a typo). Imported names, stdlib
    names, and names used in the file's own code are skipped: they resolve
    outside snapshot analysis. Bare calls need reference-verb context.
    """
    findings: list[Finding] = []
    dead = _commented_code_line_set(facts)
    code = _code_text(facts)
    texts: list[tuple[str, int, int]] = []  # (text, line, end)
    for c in facts.comments:
        if c.line in dead and c.end_line in dead:
            continue  # commented-out code block, not a reference
        texts.append((c.text, c.line, c.end_line))
    # Go doc comments are the `//` lines above a func, already scanned one
    # line each as comments; scanning the joined doc again reported every
    # claim in them twice, at two different lines.
    for f in (facts.functions if facts.language != "go" else ()):
        if f.docstring:
            scrubbed = _scrub_docstring(f.docstring, facts.language)
            if scrubbed.strip():
                texts.append((scrubbed, f.docstring_lineno or f.lineno, f.docstring_lineno or f.lineno))
    for text, line, end in texts:
        if not text.strip():
            continue
        # backticked symbols
        for m in _BACKTICK_SYMBOL.finditer(text):
            raw = m.group(1)
            name = raw.strip(".,:;!?")
            base = name.split(".")[-1]
            is_call = name.endswith("()")
            if is_call:
                name = name[:-2]
                base = base[:-2] if base.endswith("()") else base
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", name or ""):
                continue
            if len(base) < 3:
                continue
            if "xxx" in base.lower():
                continue  # Xxx placeholder convention (protobuf)
            if base.lower() in _METASYNTACTIC_CALLS:
                continue  # foo()/bar()/blah(): metasyntactic, never a ref
            if _is_reserved(base, facts.language):
                continue
            if _external_or_family(base, facts, index):
                continue
            # v2: non-call backticked names are fields/attrs/prose, except
            # dunders: dunder names are almost always real protocol
            # methods, so a dunder with no definition anywhere is worth
            # flagging (typo class).
            if not is_call:
                if not _is_dunder(base) or base in _DUNDER_OK:
                    continue
                # Only a *typo* of a known dunder is a claim. Runtime and
                # framework attributes (`__annotations__`, `__wrapped__`,
                # `__pydantic_fields__`) and JS directory names (`__tests__`)
                # have no definition by design: measured 13 of 13 non-typo
                # dunder findings across pydantic, pytest, black and vite
                # were false. One-edit typos of a real dunder keep firing.
                if facts.language != "python" or not _dunder_typo_of(base, index):
                    continue
            # Negated claims assert absence ("no call to X", "don't use
            # X"): flagging them contradicts a true statement. Same
            # window as the bare-call branch.
            window = text[max(0, m.start() - 60):m.end() + 40]
            if _NEGATED.search(window):
                continue
            # History notes and ticket-anchored discussion ("We used to
            # use `cgi.parse_header()`", SES `lockdown()` with See #5109,
            # React-compat semantics with an issue link) describe the
            # past or point outside on purpose — measured: httpx, preact.
            # The block (contiguous comment run) is the unit, not the
            # line: the ticket usually sits lines below the claim.
            # Work-item blocks keep full checking: a TODO may itself
            # name a renamed API.
            block = _comment_block_text(facts, line, end) or text
            if _HISTORICAL.search(block):
                continue
            if _TICKET.search(block) and not _WORKITEM.search(block):
                continue
            root = name.split(".")[0]
            if root in facts.imports:
                continue  # resolves via import; outside snapshot analysis
            if facts.language == "python" and root in _STDLIB_MODULES:
                continue
            if facts.language == "python" and _stdlib_class(root):
                continue  # stdlib class form (Tarfile -> tarfile)
            if _appears_in_code(root, code) or (root != base and _appears_in_code(base, code)):
                continue  # param, local, attribute, or same-file identifier
            if _appears_as_suffix(base, code):
                continue  # variant-family member (je_ wrappers, _withSecret)
            if index.has_symbol(name) or index.has_symbol(base):
                continue
            if "." in name and facts.language == "javascript" and root in JS_GLOBALS:
                continue  # `Math.random`, `Symbol.iterator`: platform API
            if "." in name and _foreign_root(root, index):
                continue  # `jax.numpy.select()`: a namespace this repo does not own
            if "." in name and not is_call:
                continue  # dotted non-call that survived: path/module prose
            hint = _suggest(base, index)
            findings.append(Finding(
                path=facts.path, line=line, end_line=end,
                checker="stale-symbol-ref", severity="lie",
                title=f"Comment references `{name}` which is not defined here",
                claim=f"`{raw}`",
                evidence=f"`{root}` is not defined, imported, or used in this file, "
                         f"and no definition of `{base}` was found in {len(index.files)} indexed source files.",
                fix=f"Update the comment to the current name, or remove the reference.{hint}",
                confidence=0.9 if is_call else 0.85,
            ))
        # bare calls: reference-verb context required (v1's distinctive-only
        # tier produced drift noise on test locals and option names).
        for m in _SYMBOL_CALL.finditer(text):
            full = m.group(1)
            base = full.split(".")[-1]
            if len(base) < 3:
                continue
            if "xxx" in base.lower():
                continue  # Xxx placeholder convention (protobuf)
            if base.lower() in _METASYNTACTIC_CALLS:
                continue  # foo()/bar()/blah(): metasyntactic, never a ref
            if _is_reserved(base, facts.language):
                continue
            if _external_or_family(base, facts, index):
                continue
            if f"`{full}()`" in text or f"`{base}()`" in text:
                continue
            if m.start() > 0 and text[m.start() - 1] == "#":
                # `Type#method()` instance-method notation (JSDoc, Ruby):
                # a method on an external type unless the type is ours.
                # Seen: prettier's "Uses `Array#toSorted()`".
                owner = re.search(r"([A-Za-z_$][A-Za-z0-9_$]*)$", text[:m.start() - 1])
                if not owner or owner.group(1) not in index.all_symbols:
                    continue
            window = text[max(0, m.start() - 60):m.end() + 40]
            has_verb = bool(REFERENCE_VERBS.search(window)) or bool(re.search(r"@deprecated|@see|see\s+`?", window, re.IGNORECASE))
            if not has_verb:
                continue
            # v2: bare claims anchored to a ticket/URL point outside the
            # snapshot on purpose (history, upstream tickets). Backticked
            # claims are still checked.
            if _TICKET.search(text):
                continue
            # v2: acronym-led bare calls are SQL/C/system APIs (VALUES(),
            # JSON_TYPE(), BOX2D_left()), never repo functions in prose.
            # Backticked calls are exempt (explicit code formatting).
            if re.match(r"[A-Z]{2,}", base):
                continue
            # v2: negated contexts discuss other systems, not repo existence.
            if _NEGATED.search(window):
                continue
            # Past-tense frames are history, not live claims.
            if _HISTORICAL.search(window):
                continue
            root = full.split(".")[0]
            if root in facts.imports:
                continue
            if facts.language == "python" and root in _STDLIB_MODULES:
                continue
            if facts.language == "python" and _stdlib_class(root):
                continue
            if _appears_in_code(root, code) or (root != base and _appears_in_code(base, code)):
                continue
            if _appears_as_suffix(base, code, minimum=4):
                continue
            if index.has_symbol(full) or index.has_symbol(base):
                continue
            if "." in full and facts.language == "javascript" and root in JS_GLOBALS:
                continue
            if "." in full and _foreign_root(root, index):
                continue
            hint = _suggest(base, index)
            findings.append(Finding(
                path=facts.path, line=line, end_line=end,
                checker="stale-symbol-ref", severity="lie",
                title=f"Comment references {full}() which is not defined here",
                claim=f"{full}()",
                evidence=f"`{root}` is not defined, imported, or used in this file, "
                         f"and no definition of `{base}` was found in indexed source files.",
                fix=f"Verify the current function name and update the comment.{hint}",
                confidence=0.8,
            ))
    return _dedupe(findings)
