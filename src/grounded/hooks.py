"""Agent hook adapters: turn an agent's post-edit event into a gate.

`grounded hook claude-code` is the command `init-agent --claude` installs
as a Claude Code PostToolUse hook. Claude Code feeds a hook's stderr back
to the model only when the hook exits 2; any other non-zero exit is shown
to the human and the agent keeps going blind. A plain
`grounded scan . --changed --quiet` exits 1 on findings (and prints only
counts), so the lie never reached the agent that wrote it. This adapter
closes that loop:

* reads the hook payload (JSON on stdin) for the edited file and cwd,
* scans the file's project and keeps findings on lines changed since HEAD
  plus rename fallout in other files (the `--changed` rules), so the agent
  is not nagged about rot it did not touch,
* exits 2 with the findings on stderr when any reach the threshold
  (default: lies only), 0 otherwise.

It never blocks on its own failure: a malformed payload, a config error
or a crashed checker exits 1 (non-blocking, visible to the human) rather
than wedging the agent loop.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .config import Config, ConfigError
from .delta import GitError, changed_lines, changed_symbols, filter_changed
from .models import SEVERITY_RANK, CheckerError, Finding
from .scanner import apply_suppressions, project_root_for, scan_root

BLOCK = 2      # Claude Code: stderr is fed back to the model
NONBLOCK = 1   # Claude Code: stderr is shown to the user only


def _format(findings: list[Finding]) -> str:
    lines = [f"grounded: {len(findings)} reference(s) in your edit do not match this repository:"]
    for f in findings:
        lines.append(f"- {f.path}:{f.line} [{f.checker}] {f.title}")
        if f.evidence:
            lines.append(f"    evidence: {f.evidence}")
        if f.fix:
            lines.append(f"    fix: {f.fix}")
    lines.append("Fix these before continuing, or tell the user why a reference is "
                 "intentional (suppress with `# grounded-disable: <checker>`).")
    return "\n".join(lines)


def claude_code(stdin_text: str, fail_on: str = "lie") -> tuple[int, str]:
    """(exit code, stderr text) for one Claude Code PostToolUse event."""
    try:
        payload = json.loads(stdin_text or "{}")
    except ValueError:
        return NONBLOCK, "grounded hook: payload is not JSON; skipped."
    if not isinstance(payload, dict):
        return NONBLOCK, "grounded hook: payload is not a JSON object; skipped."
    tool_input = payload.get("tool_input") or {}
    raw = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not raw:
        return 0, ""
    cwd = Path(payload.get("cwd") or os.getcwd())
    target = Path(raw)
    if not target.is_absolute():
        target = cwd / target
    if not target.is_file():
        return 0, ""  # deleted or not a file: nothing to verify
    root = project_root_for(target.parent)
    try:
        config = Config.load(root)
    except ConfigError as exc:
        return NONBLOCK, f"grounded hook: {exc}"
    errors: list[CheckerError] = []
    findings, facts, _ = scan_root(root, config, checker_errors=errors)
    findings, _ = apply_suppressions(findings, {f.path: f for f in facts})
    try:
        rel = target.resolve().relative_to(root).as_posix()
    except ValueError:
        return 0, ""
    try:
        hunks, untracked = changed_lines(root, "HEAD")
        symbols = changed_symbols(root, "HEAD")
        findings = filter_changed(findings, hunks, untracked, symbols)
    except GitError:
        # No git (or no HEAD yet): the edited file is all the agent's work.
        findings = [f for f in findings if f.path == rel]
    threshold = SEVERITY_RANK.get(fail_on, 3)
    blocking = [f for f in findings if SEVERITY_RANK.get(f.severity, 0) >= threshold]
    if errors:
        note = (f"grounded hook: {len(errors)} checker error(s); this check is incomplete "
                f"(first: {errors[0].checker} at {errors[0].path}).")
        if blocking:
            return BLOCK, _format(blocking) + "\n" + note
        return NONBLOCK, note
    if blocking:
        return BLOCK, _format(blocking)
    return 0, ""
