"""CLI for grounded. Stdlib only (argparse)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .checkers import CHECKER_DESCRIPTIONS, CHECKERS, REMOVED_CHECKERS
from .config import Config
from .delta import (
    DEFAULT_BASELINE_NAME,
    GitError,
    changed_lines,
    filter_changed,
    load_baseline,
    split_baselined,
    write_baseline,
)
from .models import SEVERITY_RANK
from .reporters import format_terminal, to_html, to_json, to_sarif
from .scanner import apply_suppressions, collect_files, scan_root


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="grounded",
        description="grounded: find dangling references in code comments. Deterministic, offline, zero dependencies.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="scan a directory for dangling references")
    s.add_argument("path", nargs="?", default=".", help="directory to scan (default: .)")
    s.add_argument("--format", choices=["terminal", "json", "sarif", "html"], default="terminal")
    s.add_argument("--output", "-o", default=None, help="write report to file instead of stdout")
    s.add_argument("--fail-on", choices=["lie", "drift", "smell", "never"], default=None,
                   help="minimum severity that fails the run (default: from config, else 'lie')")
    s.add_argument("--config", default=None, help="explicit config file (grounded.toml)")
    s.add_argument("--enable", default=None, help="comma-separated checker ids to run exclusively")
    s.add_argument("--disable", default=None, help="comma-separated checker ids to skip")
    s.add_argument("--baseline", default=None, metavar="FILE",
                   help="report only findings not recorded in FILE (see 'grounded baseline')")
    s.add_argument("--show-baselined", action="store_true",
                   help="with --baseline, also list suppressed findings on stderr")
    s.add_argument("--changed", nargs="?", const="HEAD", default=None, metavar="BASE",
                   help="report only findings on lines changed vs BASE (default: HEAD, uncommitted work). "
                        "The tree is still fully scanned; reporting is filtered. Errors outside git.")
    s.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    s.add_argument("--quiet", "-q", action="store_true", help="only print findings count + failures")
    s.add_argument("--jobs", type=int, default=None, metavar="N",
                   help="parallel workers (default: auto by file count)")

    sub.add_parser("init", help="write a starter grounded.toml in the current directory").add_argument(
        "--force", action="store_true", help="overwrite existing grounded.toml")

    b = sub.add_parser("baseline", help="record current findings so later scans gate on new ones only")
    b.add_argument("path", nargs="?", default=".", help="directory to scan (default: .)")
    b.add_argument("--output", "-o", default=None,
                   help=f"baseline file to write (default: <path>/{DEFAULT_BASELINE_NAME})")
    b.add_argument("--config", default=None, help="explicit config file (grounded.toml)")
    b.add_argument("--enable", default=None, help="comma-separated checker ids to run exclusively")
    b.add_argument("--disable", default=None, help="comma-separated checker ids to skip")

    fx = sub.add_parser("fix", help="rewrite unambiguous stale file references (preview with --dry-run)")
    fx.add_argument("path", nargs="?", default=".", help="directory to scan (default: .)")
    fx.add_argument("--dry-run", action="store_true", help="print fixes without writing")
    fx.add_argument("--config", default=None, help="explicit config file (grounded.toml)")

    mc = sub.add_parser("mcp", help="serve grounded over stdio as an MCP server for coding agents")
    mc.add_argument("--root", default=".", help="server root; all paths stay inside it (default: .)")

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

    findings, facts, index = scan_root(root, config, jobs=args.jobs)
    n_files = len(facts)

    suppressed_note = ""
    facts_by_path = {f.path: f for f in facts}
    findings, n_suppressed = apply_suppressions(findings, facts_by_path)
    if n_suppressed:
        suppressed_note = f" ({n_suppressed} suppressed by grounded-disable)"
    if args.changed is not None:
        try:
            hunks, untracked = changed_lines(root, args.changed)
        except GitError as exc:
            print(f"grounded: --changed unavailable: {exc}", file=sys.stderr)
            return 2
        before = len(findings)
        findings = filter_changed(findings, hunks, untracked)
        suppressed_note = f" ({before - len(findings)} outside changed lines hidden)"
    if args.baseline:
        try:
            fps = load_baseline(Path(args.baseline))
        except ValueError as exc:
            print(f"grounded: {exc}", file=sys.stderr)
            return 2
        findings, suppressed = split_baselined(findings, fps)
        suppressed_note += f" ({len(suppressed)} baselined hidden)"
        if args.show_baselined:
            for f in suppressed:
                print(f"baselined: {f.path}:{f.line} [{f.checker}] {f.title}", file=sys.stderr)

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
            out = f"grounded: {len(findings)} finding(s), {counts['lie']} lie(s), {counts['drift']} drift(s), {counts['smell']} smell(s) in {n_files} file(s)."
        else:
            out = format_terminal(findings, n_files, root=str(root), use_color=use_color)
    if suppressed_note and fmt in ("terminal",):
        out += f"\ngrounded:{suppressed_note}."
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


def cmd_baseline(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    if not root.exists():
        print(f"grounded: path does not exist: {args.path}", file=sys.stderr)
        return 2
    if root.is_file():
        root = root.parent
    config = Config.load(root, explicit=args.config)
    _resolve_enable_disable(config, args.enable, args.disable)
    findings, facts, index = scan_root(root, config)
    findings, _ = apply_suppressions(findings, {f.path: f for f in facts})
    target = Path(args.output) if args.output else (root / DEFAULT_BASELINE_NAME)
    stats = write_baseline(target, findings)
    if target.exists() and (stats["added"] or stats["removed"]):
        print(f"grounded: wrote {target}: {stats['total']} recorded "
              f"(+{stats['added']} new, -{stats['removed']} stale removed)")
    else:
        print(f"grounded: wrote {target}: {stats['total']} recorded")
    print("grounded: commit this file; scans with --baseline gate on new findings only.")
    return 0


def cmd_fix(args: argparse.Namespace) -> int:
    from .fix import apply_fixes, apply_symbol_fixes, file_fix_candidates, symbol_fix_candidates
    root = Path(args.path).resolve()
    if not root.exists():
        print(f"grounded: path does not exist: {args.path}", file=sys.stderr)
        return 2
    if root.is_file():
        root = root.parent
    config = Config.load(root, explicit=args.config)
    findings, facts, index = scan_root(root, config)
    facts_by_path = {f.path: f for f in facts}
    findings, _ = apply_suppressions(findings, facts_by_path)
    fixes = file_fix_candidates(findings, root)
    sym_fixes = symbol_fix_candidates(findings, root, index)
    if not fixes and not sym_fixes:
        print("grounded fix: nothing unambiguous to rewrite.")
        return 0
    for f, replacement, ln in fixes:
        print(f"{'would rewrite' if args.dry_run else 'rewrote'} "
              f"{f.path}:{ln}: {f.claim} -> {replacement}")
    for f, old_seg, new_seg, ln in sym_fixes:
        print(f"{'would rewrite' if args.dry_run else 'rewrote'} "
              f"{f.path}:{ln}: {old_seg}() -> {new_seg}()")
    n = apply_fixes(root, fixes, dry_run=args.dry_run)
    n += apply_symbol_fixes(root, sym_fixes, dry_run=args.dry_run)
    print(f"grounded fix: {n} file(s) {'would change' if args.dry_run else 'changed'}.")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    target = Path.cwd() / "grounded.toml"
    if target.exists() and not args.force:
        print(f"grounded: {target} already exists (use --force to overwrite)", file=sys.stderr)
        return 2
    target.write_text(
        '# grounded configuration\n'
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
        print("\nseverities: lie (error, provably false) > drift (warning, stale by evidence) > smell (note, fragile, will rot)")
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
    if args.cmd == "baseline":
        return cmd_baseline(args)
    if args.cmd == "fix":
        return cmd_fix(args)
    if args.cmd == "mcp":
        from .mcp import serve
        return serve(Path(args.root).resolve())
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
