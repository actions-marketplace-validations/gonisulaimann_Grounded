"""stale-cli-ref (experimental) and the stale-cli-flag entry point.
"""
from __future__ import annotations

import re

from ..models import FileFacts, Finding
from ..repo_index import RepoIndex
from ._shared import (
    _dedupe,
)
from .docs import (
    _fence_info_word,
    _fence_scan,
)


# ---------------------------------------------------------------- stale-cli-ref
# EXPERIMENTAL, opt-in only. Documented `grounded` invocations that would
# fail: unknown subcommands, unknown flags. The spec is introspected from
# argparse itself, so the checker cannot drift from the CLI. Agent
# instructions live or die by these lines.

_CLI_SPEC: dict | None = None
_CLI_SHELL_OPS = set("|&;><()#")


def _cli_spec() -> dict:
    global _CLI_SPEC
    if _CLI_SPEC is not None:
        return _CLI_SPEC
    import argparse
    from ..cli import build_parser
    parser = build_parser()
    spec: dict = {"flags": {}, "cmds": {}}
    for action in parser._actions:
        for opt in action.option_strings:
            spec["flags"][opt] = action
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                flags: dict = {}
                for sact in sub._actions:
                    for opt in sact.option_strings:
                        flags[opt] = sact
                spec["cmds"][name] = flags
    _CLI_SPEC = spec
    return spec


_CLI_CONSOLE_TAGS = ("", "console", "bash", "sh", "shell", "text", "plaintext",
                     "terminal", "zsh")
_CONSOLE_FENCE_TAGS = frozenset(_CLI_CONSOLE_TAGS)


def _cli_argv_segments(line: str) -> list[list[str]]:
    """Split a line into `grounded ...` argv lists (shell-aware)."""
    import shlex
    out: list[list[str]] = []
    try:
        tokens = shlex.split(line, posix=True)
    except ValueError:
        return out
    i = 0
    while i < len(tokens):
        if tokens[i] == "grounded":
            argv = ["grounded"]
            i += 1
            while i < len(tokens):
                tok = tokens[i]
                if (tok in _CLI_SHELL_OPS or tok.startswith("$")
                        or tok.startswith("`") or "\\n" in tok):
                    break
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tok):
                    i += 1
                    continue
                argv.append(tok)
                i += 1
            out.append(argv)
        elif tokens[i:i + 3] == ["python", "-m", "grounded.cli"]:
            argv = ["grounded", *tokens[i + 3:]]
            cut = len(argv)
            for j in range(1, len(argv)):
                if argv[j] in _CLI_SHELL_OPS:
                    cut = j
                    break
            out.append(argv[:cut])
            i += 3
        else:
            i += 1
    return out


def _cli_takes_value(action) -> bool:
    import argparse as _ap
    return not isinstance(action, (_ap._StoreTrueAction, _ap._StoreFalseAction,
                                   _ap._CountAction, _ap._HelpAction, _ap._VersionAction))


def _cli_check_argv(argv: list[str], spec: dict) -> str | None:
    """None if valid, else the offending token."""
    rest = argv[1:]
    flags = spec["flags"]
    if rest and not rest[0].startswith("-"):
        if rest[0] not in spec["cmds"]:
            return rest[0]
        flags = spec["cmds"][rest[0]]
        rest = rest[1:]
    elif not rest:
        return None
    skip_next = False
    for tok in rest:
        if skip_next:
            skip_next = False
            continue
        if tok == "--":
            break
        if not tok.startswith("-") or tok == "-":
            continue  # positionals/values: not verifiable, never flagged
        if tok in flags:
            if _cli_takes_value(flags[tok]):
                skip_next = True
            continue
        if tok.startswith("--"):
            matches = [o for o in flags if o.startswith(tok)]
            if len(matches) == 1:
                continue  # unambiguous argparse prefix
            return tok
        return tok
    return None


def check_stale_cli_ref(facts: FileFacts, index: RepoIndex) -> list[Finding]:
    """Documented `grounded` invocations with unknown subcommands/flags.

    Markdown only. Strong contexts (fenced console blocks, `$` lines,
    backticked spans starting with `grounded`) get full checking
    including unknown subcommands. Weak contexts (prose mentions) are
    checked only when the word after `grounded` is already a known
    subcommand or flag: "the grounded skill teaches" is prose, not an
    invocation, and never reports. Synopsis meta-syntax (`[--flag]`,
    `UPPER` placeholders) is positional and never flagged.

    Fenced blocks are identified by the shared CommonMark walk
    (`_fence_scan`), not a line-by-line toggle: a fence-looking line
    inside an open block is content, and treating it as a boundary used
    to invert the checker's view of every line after it — the exact
    failure the `unclosed-fence` checker exists for.
    """
    if facts.language != "markdown":
        return []
    findings: list[Finding] = []
    spec = _cli_spec()
    blocks, _ = _fence_scan(facts.lines)
    console_lines: set[int] = set()
    for b in blocks:
        if _fence_info_word(b["info"]) in _CONSOLE_FENCE_TAGS:
            console_lines.update(range(b["open"] + 1, (b["close"] or len(facts.lines) + 1)))
    for lineno, text in enumerate(facts.lines, start=1):
        segments: list[tuple[list[str], bool]] = []
        for m in re.finditer(r"`([^`\n]+)`", text):
            span = m.group(1)
            if "grounded" in span:
                for argv in _cli_argv_segments(span):
                    segments.append((argv, span.strip().startswith("grounded")))
        stripped = text.strip()
        if stripped.startswith("$") or (lineno in console_lines and "grounded" in text):
            body = stripped[1:].strip() if stripped.startswith("$") else stripped
            for argv in _cli_argv_segments(body):
                if argv and argv[0] == "grounded":
                    segments.append((argv, body.startswith("grounded")))
        for argv, strong in segments:
            if len(argv) < 2:
                continue
            if argv[1].endswith(":"):
                continue  # program output (`grounded fix: ...`), not an invocation
            if not strong and not (argv[1] in spec["cmds"] or argv[1].startswith("-")):
                continue  # prose mention, not an invocation
            bad = _cli_check_argv(argv, spec)
            if bad is None:
                continue
            findings.append(Finding(
                path=facts.path, line=lineno, end_line=lineno,
                checker="stale-cli-ref", severity="lie",
                title=f"Documented invocation uses unknown `{bad}`",
                claim=" ".join(argv[:4]),
                evidence=f"`{bad}` matches no subcommand or flag in this version of grounded.",
                fix="Fix the invocation or remove the example.",
                confidence=0.85,
            ))
    return _dedupe(findings)


def check_stale_cli_flag(facts: FileFacts, index: RepoIndex) -> list[Finding]:
    """See grounded/cli_flags.py (kept in its own module)."""
    from ..cli_flags import check_stale_cli_flag as _impl
    return _impl(facts, index)
