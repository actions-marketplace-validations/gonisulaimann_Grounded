"""number-drift and fragile-anchor: comment numbers and anchors that rot.
"""
from __future__ import annotations

import re

from ..models import Comment, FileFacts, Finding
from ..repo_index import RepoIndex
from ._shared import (
    _ILLUSTRATIVE,
    _LINE_ANCHOR,
    _SEE_ABOVE_BELOW,
    _TEMPORAL,
    _TICKET,
    _comment_lines,
    _commented_code_line_set,
    _dedupe,
    _nearby_ticket,
    _strip_strings,
    _workaround_match,
)


_NUMBER_CLAIM = re.compile(
    r"\b(timeout|time-out|port|retries|max[_ -]?(?:retries|attempts|connections|size|length|count|workers?|threads?)|"
    r"min[_ -]?(?:size|length|count)?|limit|workers?|threads?|pool[_ -]?size|batch[_ -]?size|"
    r"cache[_ -]?size|ttl|expir\w+|delay|interval|threshold)\b\s*(?:is|of|=|:|→|->)?\s*(\d+(?:\.\d+)?)\s*(ms|s|sec|secs|seconds?|minutes?|mins?|hours?|ms|bytes?|kb|mb|gb|px|%)?",
    re.IGNORECASE,
)
_NUMBER_IN_CODE = re.compile(r"(?<![A-Za-z0-9_.])(\d+(?:\.\d+)?)(?![A-Za-z0-9_.])")


def check_number_drift(facts: FileFacts, index: RepoIndex) -> list[Finding]:
    findings: list[Finding] = []
    lines = facts.lines
    dead = _commented_code_line_set(facts)
    for c in facts.comments:
        if c.line in dead:
            continue
        if len(c.text) > 300:
            continue
        # Example sentences invent numbers ("Example, if we run for 1ms").
        # The marker may sit on a neighboring comment line.
        window_text = c.text
        for ln in range(c.line - 2, c.end_line + 3):
            if 1 <= ln <= len(facts.lines):
                s = facts.lines[ln - 1].strip()
                if s.startswith(("#", "//", "*", "/*")):
                    window_text += "\n" + s
        if _ILLUSTRATIVE.search(window_text):
            continue
        for m in _NUMBER_CLAIM.finditer(c.text):
            keyword = m.group(1).lower().replace("-", "_").replace(" ", "_")
            try:
                claimed = float(m.group(2))
            except ValueError:
                continue
            unit = (m.group(3) or "").lower()
            # bidirectional window: signature defaults live above, assignments below
            key_simple = re.split(r"[_ ]", keyword)[0]  # timeout/max/port/...
            if len(key_simple) < 3:
                continue
            lo = max(0, c.line - 1 - 15)
            hi = min(len(lines), c.line + 15)
            window = "\n".join(lines[lo:hi])
            if key_simple not in window.lower():
                continue
            # The keyword's own value on each code line: the first number
            # right after the keyword (`timeout=0.01`, `TIMEOUT = 30`), not
            # any number on the line (celery: "timeout=0 means do not
            # block" beside `timeout=0, interval=0.01` read as drift).
            bound: list[tuple[int, str, str]] = []  # (line index, number, code)
            key_re = re.compile(re.escape(key_simple) + r"[A-Za-z0-9_]*[\s\"']*(?:==|<=|>=|[=:<>])\s*\(?\s*(\d+(?:\.\d+)?)(?![A-Za-z0-9_.])",
                                re.IGNORECASE)
            for j in range(lo, hi):
                if (j + 1) in dead or c.line <= j + 1 <= c.end_line:
                    continue
                code_line = lines[j]
                if code_line.strip().startswith(("#", "//", "*", "/*")):
                    continue
                for km in key_re.finditer(_strip_strings(code_line)):
                    bound.append((j, km.group(1), code_line))
            if not bound:
                continue
            values = []
            for j, raw, code_line in bound:
                try:
                    values.append((float(raw), j, raw, code_line))
                except ValueError:
                    continue
            if any(v == claimed for v, *_ in values):
                continue  # the claim matches a binding in reach: no drift
            for actual, j, raw, code_line in values:
                if 1900 < actual < 2100 and 1900 < claimed < 2100:
                    break
                if actual in (0, 1) and claimed in (0, 1):
                    break
                findings.append(Finding(
                    path=facts.path, line=c.line, end_line=c.end_line,
                    checker="number-drift", severity="drift",
                    title=f"Comment says {key_simple} {m.group(2)}{unit or ''} but code uses {raw}",
                    claim=f"{key_simple} = {m.group(2)}{unit or ''}",
                    evidence=f"{facts.path}:{j + 1}: {code_line.strip()[:120]}",
                    fix="Update the comment to the real value, or better: define a named constant and reference it from both.",
                    confidence=0.6,
                ))
                break
    return _dedupe(findings)


def check_fragile_anchor(facts: FileFacts, index: RepoIndex) -> list[Finding]:
    # Ticket links often sit on the line next to the marker ("See
    # https://..." below a "Temporarily ..." comment). Judge the marker
    # together with immediately adjacent comment lines.
    by_line = _comment_lines(facts)

    def has_ticket_nearby(c: Comment) -> bool:
        return _nearby_ticket(facts, c.line, c.end_line, by_line)

    findings: list[Finding] = []
    for c in facts.comments:
        text = c.text
        if not text.strip():
            continue
        m = _LINE_ANCHOR.search(text)
        if m:
            findings.append(Finding(
                path=facts.path, line=c.line, end_line=c.end_line,
                checker="fragile-anchor", severity="smell",
                title=f"Comment anchors to `{m.group(0)}` which rots on every edit",
                claim=m.group(0),
                evidence="Line numbers shift on every edit; the anchor is guaranteed to drift.",
                fix="Reference a symbol name (`function_name`) instead of a line number.",
                confidence=0.9,
            ))
            continue
        if _SEE_ABOVE_BELOW.search(text):
            findings.append(Finding(
                path=facts.path, line=c.line, end_line=c.end_line,
                checker="fragile-anchor", severity="smell",
                title="Comment says “see above/below” with no symbol to find",
                claim="see above/below",
                evidence="Positional references break when code moves.",
                fix="Name the symbol or file the reader should look at.",
                confidence=0.65,
            ))
            continue
        if _workaround_match(text) and not _TICKET.search(text) and not has_ticket_nearby(c):
            mm = _workaround_match(text)
            findings.append(Finding(
                path=facts.path, line=c.line, end_line=c.end_line,
                checker="fragile-anchor", severity="smell",
                title=f"Untracked workaround (“{mm.group(0)}”) with no ticket or link",
                claim=mm.group(0),
                evidence="Workarounds without a durable referent can never be safely removed.",
                fix="Add the issue URL or version condition (e.g. `# TODO(#123): remove when …`).",
                confidence=0.7,
            ))
            continue
        if _TEMPORAL.search(text) and not _TICKET.search(text) and not has_ticket_nearby(c):
            # only flag short comments where temporality is the point; avoid prose FPs
            if len(text) < 160 and re.search(r"\b(fix|hack|patch|toggle|flag|skip|disable)\b", text, re.IGNORECASE):
                mm = _TEMPORAL.search(text)
                findings.append(Finding(
                    path=facts.path, line=c.line, end_line=c.end_line,
                    checker="fragile-anchor", severity="smell",
                    title=f"Temporal marker (“{mm.group(0)}”) with no expiry condition",
                    claim=mm.group(0),
                    evidence="“Temporary” without a condition is permanent.",
                    fix="State the removal condition: version, date, or ticket.",
                    confidence=0.6,
                ))
    return _dedupe(findings)
