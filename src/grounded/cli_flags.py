"""stale-cli-flag: documented flags of the repo's own CLIs that no parser defines.

EXPERIMENTAL, opt-in. `stale-cli-ref` verifies documented `grounded`
invocations against grounded's live argparse tree. This checker does the
same for *any* repository's command-line programs, statically:

1. **Programs** come from the repo's manifests: `[project.scripts]` keys in
   every pyproject.toml, `bin` in every package.json (a string `bin` is
   named after the package), and Go `cmd/<name>/` directories. A doc line
   is only checked when it invokes one of these names; other tools'
   flags (`pip install --upgrade`) are never judged.
2. **Flags** are collected from every source file: argparse and pytest
   (`add_argument`, `addoption`), click (`option`, `--a/--no-a` pairs),
   Go `flag` and cobra/pflag (`Flags().StringVarP(&v, "name", ...)`), and
   commander/cac (`.option('-p, --port <n>')`). The inventory is the union
   over the whole repo: per-subcommand scoping would need the command
   tree, and a flag defined anywhere is never reported.
3. **Silence when the inventory cannot be complete.** A repo whose CLI
   derives flags from something we do not read (typer and fire use
   function parameters, docopt parses the usage text, yargs/minimist/
   meow accept or declare flags dynamically) reports nothing at all.
   An inventory with no flags is also silence, never "everything is
   unknown".

Only long flags (`--name`) are judged: short-flag clusters (`-xvf`) and
Go's single-dash long flags are ambiguous in prose. argparse's unique
prefix abbreviations, `--no-<flag>` negations (BooleanOptionalAction,
click's `/--no-x`, cobra booleans), and `--flag=value` forms all resolve.
Synopsis meta-syntax (`[--flag]`) is a positional sketch and stays silent,
matching `stale-cli-ref`.
"""
from __future__ import annotations

import json
import posixpath
import re

from .models import FileFacts, Finding

# Frameworks whose flags are not written as strings we can read.
_OPAQUE_CLI = re.compile(
    r"^\s*(?:import|from)\s+(typer|fire|docopt)\b"
    # click commands that forward unknown options/extra args to code we
    # cannot see (seen: celery `control` passing `--terminate` through to
    # remote-control function parameters).
    r"|\b(?:ignore_unknown_options|allow_extra_args)['\"]?\s*[=:]\s*True"
    r"|require\(\s*['\"](?:yargs|minimist|meow|mri|arg)['\"]\s*\)"
    r"|from\s+['\"](?:yargs|minimist|meow|mri|arg)(?:/[^'\"]*)?['\"]",
    re.M)

# Any *option/*argument call: click.option, typer-free Option subclasses
# (celery's `DaemonOption("--pidfile")`, `CeleryOption(...)`), argparse
# add_argument, pytest addoption.
_PY_FLAG_CALL = re.compile(
    r"\b(?:add_argument|addoption|_addoption|\w*[Oo]ption|\w*[Aa]rgument)\s*\(([^)]*)\)", re.S)
# Go: the `flag`/`pflag` packages, cobra's `.Flags()`/`.PersistentFlags()`,
# and any FlagSet variable (`cmdFlags.BoolVar(&c.x, "name", ...)`, seen:
# terraform). Only flag-shaped method names are read.
_GO_FLAG_CALL = re.compile(
    r"\b(?:[A-Za-z_][A-Za-z0-9_]*(?:\.(?:Persistent)?Flags\(\))?)\s*\.\s*"
    r"(?P<fn>(?:Bool|String|Int|Int64|Int32|Uint|Uint64|Float64|Float32|Duration|"
    r"StringSlice|StringArray|IntSlice|StringToString|Count|IP|IPNet|Var|Func|"
    r"TextVar|BoolFunc)(?:Var)?P?)\s*\(\s*(?P<args>[^)]*)\)")
_JS_FLAG_CALL = re.compile(r"\.(?:option|requiredOption|addOption)\s*\(\s*(['\"`])([^'\"`]+)\1")
_STRING = re.compile(r"""(['"])((?:\\.|(?!\1).)*)\1""")
_LONG_FLAG = re.compile(r"(?<![\w-])--[A-Za-z0-9][A-Za-z0-9_.-]*")
# A string literal that *is* a long flag: argv handled by hand
# (`if "--commands" in args`, seen: django's ManagementUtility) or a flag
# table. Code that spells the flag handles it somehow.
# Quotes only: a backticked ``--flag`` in a docstring is prose about the
# flag, not code handling it.
_FLAG_LITERAL = re.compile(r"""(['"])(--[A-Za-z0-9][A-Za-z0-9_.-]*)(?:=)?\1""")

_UNIVERSAL = frozenset({"--help", "--version"})
_PREFIX_COMMANDS = frozenset({"sudo", "exec", "time", "env", "nohup", "npx", "uvx",
                              "pipx", "run", "poetry", "pdm", "hatch", "doas"})
_SHELL_OPS = frozenset("|&;><()")


class CliInventory:
    """Programs and flags a repository defines (or None when unknowable)."""

    def __init__(self, programs: set[str], flags: set[str], argparse_prefixes: bool,
                 subcommands: set[str] | None = None):
        self.programs = programs
        self.flags = flags
        self.argparse_prefixes = argparse_prefixes
        self.subcommands = subcommands or set()

    def knows(self, flag: str) -> bool:
        if flag in self.flags or flag in _UNIVERSAL:
            return True
        if flag.startswith("--no-") and ("--" + flag[5:]) in self.flags:
            return True
        if self.argparse_prefixes and any(f.startswith(flag) for f in self.flags):
            # A unique prefix is a valid abbreviation; an ambiguous one is
            # an argparse usage error about real flags, not a stale flag
            # (seen: pipx docs demonstrating `--py` on purpose).
            return True
        return False


def _programs(index) -> set[str]:
    out: set[str] = set()
    root = index.root
    for path in _manifest_paths(root):
        name = path.name
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if name == "pyproject.toml":
            out |= _pyproject_scripts(text)
        elif name in ("setup.py", "setup.cfg"):
            out |= _setuptools_scripts(text)
        elif name == "package.json":
            try:
                data = json.loads(text)
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            b = data.get("bin")
            if isinstance(b, str) and isinstance(data.get("name"), str):
                out.add(data["name"].split("/")[-1])
            elif isinstance(b, dict):
                out |= {str(k) for k in b}
    for rel in index.rel_paths:
        m = re.match(r"(?:.*/)?cmd/([A-Za-z0-9][A-Za-z0-9_-]*)/main\.go$", rel)
        if m:
            out.add(m.group(1))
    return {p for p in out if p and len(p) >= 2}


def _manifest_paths(root):
    from .checkers import IGNORED_DIR_NAMES
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            entries = list(cur.iterdir())
        except OSError:
            continue
        for e in entries:
            if e.is_dir():
                if e.name in IGNORED_DIR_NAMES or e.name.startswith(".") or e.is_symlink():
                    continue
                stack.append(e)
            elif e.name in ("pyproject.toml", "package.json", "setup.py", "setup.cfg"):
                yield e


def _pyproject_scripts(text: str) -> set[str]:
    out: set[str] = set()
    section = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            section = s.strip("[] ").replace('"', "").replace("'", "")
            continue
        if section in ("project.scripts", "project.gui-scripts", "tool.poetry.scripts"):
            m = re.match(r"""["']?([A-Za-z0-9][A-Za-z0-9_.-]*)["']?\s*=""", s)
            if m:
                out.add(m.group(1))
        elif section == "project":
            # Dotted keys: `scripts.pytest = "..."` (seen: pytest).
            m = re.match(r"""(?:gui-)?scripts\.["']?([A-Za-z0-9][A-Za-z0-9_.-]*)["']?\s*=""", s)
            if m:
                out.add(m.group(1))
    return out


_CONSOLE_SCRIPT = re.compile(r"""^\s*['"]?([A-Za-z0-9][A-Za-z0-9_.-]*)\s*=\s*[A-Za-z_][\w.]*:[\w.]+""", re.M)


def _setuptools_scripts(text: str) -> set[str]:
    """`console_scripts` names from setup.py / setup.cfg (seen: celery)."""
    i = text.find("console_scripts")
    if i < 0:
        return set()
    return set(_CONSOLE_SCRIPT.findall(text[i:i + 2000]))


def _flags_in_python(text: str) -> tuple[set[str], bool]:
    flags: set[str] = set()
    for m in _PY_FLAG_CALL.finditer(text):
        for sm in _STRING.finditer(m.group(1)):
            val = sm.group(2)
            for part in val.split("/"):
                part = part.strip()
                if re.fullmatch(r"--[A-Za-z0-9][A-Za-z0-9_.-]*", part):
                    flags.add(part)
    boolean_optional = "BooleanOptionalAction" in text
    if boolean_optional:
        flags |= {"--no-" + f[2:] for f in list(flags)}
    return flags, "argparse" in text or "addoption" in text


def _flags_in_go(text: str) -> set[str]:
    flags: set[str] = set()
    for m in _GO_FLAG_CALL.finditer(text):
        fn, args = m.group("fn"), m.group("args")
        strings = [s.group(2) for s in _STRING.finditer(args)]
        if not strings:
            continue
        # *Var/*VarP take (&target, "name", ...); the rest take ("name", ...).
        name = strings[0]
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
            flags.add("--" + name)
            if fn.startswith("Bool"):
                flags.add("--no-" + name)
    return flags


def _flags_in_js(text: str) -> set[str]:
    flags: set[str] = set()
    for m in _JS_FLAG_CALL.finditer(text):
        for f in _LONG_FLAG.findall(m.group(2)):
            flags.add(f)
            if f.startswith("--no-"):
                flags.add("--" + f[5:])
    return flags


# Subcommand names a repo declares: cobra `Use: "name ..."`, click
# `@group.command("name")` / `name=`, argparse `add_parser("name")`,
# commander/cac `.command('name ...')`, click function-named commands.
_SUBCOMMAND_DECL = re.compile(
    r"""\bUse:\s*"([A-Za-z][\w-]*)"""
    r"""|\badd_parser\(\s*['"]([A-Za-z][\w-]*)['"]"""
    r"""|\.command\(\s*(?:name\s*=\s*)?['"`]([A-Za-z][\w-]*)"""
    r"""|@[\w.]*\.(?:command|group)\((?:[^)]*\bname\s*=\s*['"]([A-Za-z][\w-]*)['"])?[^)]*\)\s*"""
    r"""(?:@[^\n]*\n\s*)*def\s+([A-Za-z_]\w*)""")


def _subcommands(text: str) -> set[str]:
    out: set[str] = set()
    for m in _SUBCOMMAND_DECL.finditer(text):
        for g in m.groups():
            if g:
                out.add(g.replace("_", "-"))
                out.add(g)
    return out


def inventory(index) -> CliInventory | None:
    """The repo's CLI inventory, computed once per process and cached."""
    cached = index.__dict__.get("_cli_inventory", False)
    if cached is not False:
        return cached
    result: CliInventory | None = None
    programs = _programs(index)
    if programs:
        flags: set[str] = set()
        subcommands: set[str] = set()
        argparse_prefixes = False
        opaque = False
        for f in getattr(index, "files", []):
            try:
                rel = f.relative_to(index.root).as_posix()
            except ValueError:
                continue
            suffix = posixpath.splitext(rel)[1].lower()
            if suffix in (".md", ".rst", ".markdown", ".txt"):
                continue  # doc-local definitions are read per page (below)
            if suffix not in (".py", ".go", ".js", ".mjs", ".cjs", ".ts", ".mts", ".cts"):
                continue
            text = index.text_of(rel) or ""
            flags |= {m.group(2) for m in _FLAG_LITERAL.finditer(text)}
            subcommands |= _subcommands(text)
            if suffix == ".py":
                if _OPAQUE_CLI.search(text):
                    opaque = True
                    break
                got, argp = _flags_in_python(text)
                flags |= got
                argparse_prefixes = argparse_prefixes or (argp and bool(got))
            elif suffix == ".go":
                flags |= _flags_in_go(text)
            else:
                if _OPAQUE_CLI.search(text):
                    opaque = True
                    break
                flags |= _flags_in_js(text)
        if not opaque and flags:
            result = CliInventory(programs, flags, argparse_prefixes, subcommands)
    index.__dict__["_cli_inventory"] = result
    return result


def _argvs(line: str, programs: set[str]) -> list[list[str]]:
    """argv lists for every invocation of a known program on a line."""
    import shlex
    try:
        tokens = shlex.split(line, posix=True)
    except ValueError:
        return []
    out: list[list[str]] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        prog = None
        # Command position only: line start, after a shell operator, or
        # after a prefix command. `conda install gh --channel x` names gh
        # as an argument of conda (seen: cli/cli install docs).
        prev = tokens[i - 1] if i else None
        at_command = (prev is None or prev in _SHELL_OPS or prev in ("&&", "||", ";;")
                      or prev in _PREFIX_COMMANDS
                      or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", prev or "") is not None)
        if not at_command:
            i += 1
            continue
        if tok in programs:
            prog, i = tok, i + 1
        elif tok in ("python", "python3") and i + 2 < len(tokens) and tokens[i + 1] == "-m" \
                and tokens[i + 2].split(".")[0].replace("_", "-") in programs:
            prog, i = tokens[i + 2], i + 3
        if prog is None:
            i += 1
            continue
        argv = [prog]
        while i < len(tokens):
            t = tokens[i]
            if t in _SHELL_OPS or t in ("&&", "||", ";;") or t.startswith("$"):
                break
            argv.append(t)
            i += 1
        out.append(argv)
    return out


def _doc_invocation_lines(facts: FileFacts) -> list[tuple[int, str, bool]]:
    """(lineno, text, strong) candidate invocation texts in a doc file."""
    from .checkers import _CONSOLE_FENCE_TAGS, _fence_info_word, _fence_scan
    out: list[tuple[int, str, bool]] = []
    console: set[int] = set()
    other: set[int] = set()
    if facts.language == "markdown":
        blocks, _ = _fence_scan(facts.lines)
        for b in blocks:
            span = range(b["open"] + 1, (b["close"] or len(facts.lines) + 1))
            (console if _fence_info_word(b["info"]) in _CONSOLE_FENCE_TAGS else other).update(span)
    else:
        console, other = _rst_blocks(facts.lines)
    for lineno, text in enumerate(facts.lines, start=1):
        stripped = text.strip()
        if lineno in other and not stripped.startswith("$ "):
            continue  # code, unless it is a shell prompt (`.. code-block:: pytest`)
        if stripped.startswith("$ ") or stripped.startswith("> "):
            out.append((lineno, stripped[2:], True))
        elif lineno in console:
            out.append((lineno, stripped, True))
        for m in re.finditer(r"``?([^`\n]+)``?", text):
            out.append((lineno, m.group(1), False))
    return out


# Changelogs and release notes record past behavior: a removed flag named
# there is history, not a claim (seen: celery's docs/history/, pytest's
# changelog/ fragments).
_HISTORY_DOC = re.compile(
    r"(^|/)(changelog|changes|history|news|whatsnew|release[-_]?notes|releases)"
    r"(/|([-_.][^/]*)?\.(md|rst|markdown|txt)$)", re.I)
# "as in `tool --flag`", "e.g. `tool --flag`": illustrative prose.
_ILLUSTRATIVE_PROSE = re.compile(r"\b(as in|e\.g\.|for example|for instance|such as)\b", re.I)


_RST_DIRECTIVE = re.compile(r"^(\s*)\.\.\s+(code-block|code|sourcecode|prompt)::\s*(\S*)")
_RST_SHELL = frozenset({"console", "bash", "sh", "shell", "shell-session", "zsh", "text", "none", ""})


def _rst_blocks(lines: list[str]) -> tuple[set[int], set[int]]:
    """(console lines, other code lines) for reST directive bodies:
    `.. code-block:: console` / `.. prompt:: bash` bodies are invocations;
    `.. code-block:: python` bodies are code and never judged."""
    console: set[int] = set()
    other: set[int] = set()
    i = 0
    while i < len(lines):
        m = _RST_DIRECTIVE.match(lines[i])
        if not m:
            i += 1
            continue
        base = len(m.group(1))
        lang = m.group(3).lower()
        target = console if (m.group(2) == "prompt" or lang in _RST_SHELL) else other
        j = i + 1
        while j < len(lines) and (not lines[j].strip()
                                  or len(lines[j]) - len(lines[j].lstrip()) > base):
            stripped = lines[j].strip()
            if stripped and not stripped.startswith(":"):  # skip directive options
                target.add(j + 1)
            j += 1
        i = j
    return console, other


def check_stale_cli_flag(facts: FileFacts, index) -> list[Finding]:
    if facts.language not in ("markdown", "rst"):
        return []
    if _HISTORY_DOC.search(facts.path):
        return []
    inv = inventory(index)
    if inv is None:
        return []
    # A page that teaches a plugin option defines it in an example
    # (`parser.addoption("--runslow")` in a conftest block, then
    # `pytest --runslow`): those flags exist for that page's walkthrough.
    # Page-local on purpose: a doc that merely reproduces the real source
    # definition must not vouch for every other page (seen: flake8's
    # plugin guide copying `add_option('--max-line-length', ...)`).
    local = _flags_in_python("\n".join(facts.lines))[0]
    findings: list[Finding] = []
    seen: set[tuple[int, str]] = set()
    for lineno, text, strong in _doc_invocation_lines(facts):
        for argv in _argvs(text, inv.programs):
            if not strong and (len(argv) < 2 or text.strip().split()[0] != argv[0]):
                continue  # prose mention, not an invocation
            if not strong and _ILLUSTRATIVE_PROSE.search(
                    " ".join(facts.lines[max(0, lineno - 2):lineno])):
                continue  # marker on this line or the one before
            first = next((t for t in argv[1:] if not t.startswith("-")), None)
            if (inv.subcommands and first is not None and first not in inv.subcommands
                    and re.fullmatch(r"[a-z][a-z0-9-]*", first)):
                # A subcommand this repo does not declare: an extension or
                # plugin (`gh aw ...`, `celery flower ...`), whose flags
                # are not ours to judge.
                continue
            for tok in argv[1:]:
                if tok == "--":
                    break
                if not tok.startswith("--") or tok == "--":
                    continue
                flag = tok.split("=", 1)[0]
                if not re.fullmatch(r"--[A-Za-z0-9][A-Za-z0-9_.-]*", flag):
                    continue  # placeholders, globs, meta-syntax
                if inv.knows(flag) or flag in local or (lineno, flag) in seen:
                    continue
                seen.add((lineno, flag))
                findings.append(Finding(
                    path=facts.path, line=lineno, end_line=lineno,
                    checker="stale-cli-flag", severity="lie",
                    title=f"Documented `{argv[0]}` invocation uses unknown flag `{flag}`",
                    claim=" ".join(argv[:4]),
                    evidence=f"No argparse/click/pytest/flag/cobra/commander definition in this "
                             f"repo declares `{flag}` ({len(inv.flags)} flags indexed).",
                    fix="Update the example to the current flag, or remove it.",
                    confidence=0.8,
                ))
    return findings
