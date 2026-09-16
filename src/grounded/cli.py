"""CLI for grounded. Stdlib only (argparse)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .checkers import CHECKER_DESCRIPTIONS, CHECKERS, REMOVED_CHECKERS
from .config import Config
from .models import SEVERITY_RANK
from .reporters import format_terminal, to_html, to_json, to_sarif
from .scanner import collect_files, scan_root


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="grounded",
        description="grounded — the epistemic linter. Proves comments wrong with evidence. Deterministic, offline, zero dependencies.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="scan a directory for belief rot")
    s.add_argument("path", nargs="?", default=".", help="directory to scan (default: .)")
    s.add_argument("--format", choices=["terminal", "json", "sarif", "html"], default="terminal")
    s.add_argument("--output", "-o", default=None, help="write report to file instead of stdout")
    s.add_argument("--fail-on", choices=["lie", "drift", "smell", "never"], default=None,
                   help="minimum severity that fails the run (default: from config, else 'lie')")
    s.add_argument("--config", default=None, help="explicit config file (grounded.toml)")
    s.add_argument("--enable", default=None, help="comma-separated checker ids to run exclusively")
    s.add_argument("--disable", default=None, help="comma-separated checker ids to skip")
    s.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    s.add_argument("--quiet", "-q", action="store_true", help="only print findings count + failures")

    sub.add_parser("init", help="write a starter grounded.toml in the current directory").add_argument(
        "--force", action="store_true", help="overwrite existing grounded.toml")

    e = sub.add_parser("explain", help="explain what a checker proves")
    e.add_argument("checker", nargs="?", default=None, help="checker id (omit to list all)")

    l = sub.add_parser("list", help="list files that would be scanned")
    l.add_argument("path", nargs="?", default=".")
    l.add_argument("--config", default=None)
    return p


def _resolve_enable_disable(config: Config, enable: str | None, disable: str | None) -> Config:
    if enable:
        want = {x.strip() for x in enable.split(",") if x.strip()}
        config.enabled = {w for w in want if w in CHECKERS} or set(config.enabled)
    if disable:
        drop = {x.strip() for x in disable.split(",") if x.strip()}
        config.enabled -= drop
    if not config.enabled:
        config.enabled = set(CHECKERS)
    return config


def cmd_scan(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    if not root.exists():
        print(f"grounded: path does not exist: {args.path}", file=sys.stderr)
        return 2
    if root.is_file():
        root = root.parent
    config = Config.load(root, explicit=args.config)
    if args.fail_on:
        config.fail_on = args.fail_on
    _resolve_enable_disable(config, args.enable, args.disable)

    findings, facts, index = scan_root(root, config)
    n_files = len(facts)
    fmt = args.format
    if fmt == "json":
        out = to_json(findings)
    elif fmt == "sarif":
        out = to_sarif(findings, root=str(root))
    elif fmt == "html":
        out = to_html(findings, n_files, root=str(root))
    else:
        use_color = (not args.no_color) and sys.stdout.isatty()
        if args.quiet:
            counts = {"lie": 0, "drift": 0, "smell": 0}
            for f in findings:
                counts[f.severity] += 1
            out = f"grounded: {len(findings)} finding(s) — {counts['lie']} lie(s), {counts['drift']} drift(s), {counts['smell']} smell(s) in {n_files} file(s)."
        else:
            out = format_terminal(findings, n_files, root=str(root), use_color=use_color)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
    else:
        print(out)
    # exit code
    if config.fail_on == "never":
        return 0
    threshold = SEVERITY_RANK.get(config.fail_on, 3)
    for f in findings:
        if SEVERITY_RANK.get(f.severity, 0) >= threshold:
            return 1
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    target = Path.cwd() / "grounded.toml"
    if target.exists() and not args.force:
        print(f"grounded: {target} already exists (use --force to overwrite)", file=sys.stderr)
        return 2
    target.write_text(
        '# grounded — epistemic linter configuration\n'
        '# Uncomment to disable noisy checkers, or set fail_on to drift/smell/never.\n\n'
        '# disable = ["fragile-anchor"]\n'
        '# fail_on = "lie"\n'
        '# ignore_dirs = ["docs"]\n',
        encoding="utf-8",
    )
    print(f"grounded: wrote {target}")
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    if not args.checker:
        for cid in sorted(CHECKERS):
            print(f"{cid:18} {CHECKER_DESCRIPTIONS[cid]}")
        print("\nseverities: lie (error — a provable falsehood) > drift (warning — stale by evidence) > smell (note — fragile, will rot)")
        return 0
    cid = args.checker
    if cid in REMOVED_CHECKERS:
        print(f"{cid}\n  {REMOVED_CHECKERS[cid]}")
        return 0
    if cid not in CHECKERS:
        print(f"grounded: unknown checker {cid!r}. Known: {', '.join(sorted(CHECKERS))}", file=sys.stderr)
        return 2
    print(f"{cid}\n  {CHECKER_DESCRIPTIONS[cid]}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    config = Config.load(root, explicit=args.config)
    for f in collect_files(root, config):
        try:
            print(f.relative_to(root).as_posix())
        except ValueError:
            print(str(f))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "scan":
        return cmd_scan(args)
    if args.cmd == "init":
        return cmd_init(args)
    if args.cmd == "explain":
        return cmd_explain(args)
    if args.cmd == "list":
        return cmd_list(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
