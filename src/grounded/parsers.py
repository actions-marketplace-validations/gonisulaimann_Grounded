"""File parsers: extract functions + comments for Python and JavaScript/TypeScript.

Stdlib only. The JS parser is intentionally regex-based (no native deps)
and tuned for precision over recall: it extracts what it can prove,
and never invents structure it cannot see.
"""
from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

from .models import Comment, FileFacts, FuncInfo


# ---------------------------------------------------------------- Python

def _py_func_info(node: ast.FunctionDef | ast.AsyncFunctionDef, source: str) -> FuncInfo:
    args: list[str] = []
    a = node.args
    for arg in list(a.posonlyargs) + list(a.args):
        args.append(arg.arg)
    if a.vararg is not None:
        args.append("*" + a.vararg.arg)
    for arg in a.kwonlyargs:
        args.append(arg.arg)
    if a.kwarg is not None:
        args.append("**" + a.kwarg.arg)
    doc = ast.get_docstring(node, clean=False) or ""
    doc_lineno = 0
    if (
        node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    ):
        doc_lineno = node.body[0].lineno
    has_value_return = False
    has_return = False
    raises: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            if child is not node:
                continue
        if isinstance(child, ast.Return):
            has_return = True
            if child.value is not None and not (
                isinstance(child.value, ast.Constant) and child.value.value is None
            ):
                has_value_return = True
        if isinstance(child, ast.Yield | ast.YieldFrom) if hasattr(ast, "YieldFrom") else isinstance(child, ast.Yield):
            has_value_return = True
        if isinstance(child, ast.Raise):
            exc = child.exc
            name = _exc_name(exc)
            if name:
                raises.append(name)
    # dedupe preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for r in raises:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    end = getattr(node, "end_lineno", None) or node.lineno
    return FuncInfo(
        name=node.name,
        lineno=node.lineno,
        end_lineno=end,
        args=args,
        docstring=doc,
        docstring_lineno=doc_lineno,
        has_value_return=has_value_return,
        has_bare_return_only=(has_return and not has_value_return),
        raises=uniq,
    )


def _exc_name(exc: ast.expr | None) -> str | None:
    if exc is None:
        return "Exception(reraise)"
    if isinstance(exc, ast.Name):
        return exc.id
    if isinstance(exc, ast.Attribute):
        return exc.attr
    if isinstance(exc, ast.Call):
        return _exc_name(exc.func)
    if isinstance(exc, ast.Subscript):
        return _exc_name(exc.value)
    return None


def _py_comments_tolerant(text: str) -> list[Comment]:
    """Best-effort `#` extraction for syntactically broken buffers
    (mid-typing editor state). Tracks single/double-quoted and triple-quoted
    spans crudely; a `#` inside a string is skipped when detected. May miss
    exotic cases; it only ever feeds comment checkers, never code facts."""
    comments: list[Comment] = []
    triple: str | None = None
    for i, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line
        if triple is not None:
            end = line.find(triple)
            if end == -1:
                continue
            line = line[end + 3:]
            triple = None
        # strip triple-quoted spans opening on this line
        while True:
            d = line.find('"""')
            s = line.find("'''")
            if d == -1 and s == -1:
                break
            if d != -1 and (s == -1 or d < s):
                q = '"""'
            else:
                q = "'''"
            rest = line.split(q, 1)[1] if q in line else ""
            if q in rest:
                line = line[:line.find(q)] + rest.split(q, 1)[1]
            else:
                line = line[:line.find(q)]
                triple = q
                break
        # find # outside single/double-quoted spans
        in_q: str | None = None
        pos = 0
        found = -1
        while pos < len(line):
            ch = line[pos]
            if in_q is not None:
                if ch == "\\":
                    pos += 2
                    continue
                if ch == in_q:
                    in_q = None
            elif ch in ("\"", "'"):
                in_q = ch
            elif ch == "#":
                found = pos
                break
            pos += 1
        if found != -1:
            inner = line[found + 1:]
            if inner.startswith("!"):
                continue
            if inner.startswith(" "):
                inner = inner[1:]
            comments.append(Comment(text=inner, raw=line[found:], line=i, end_line=i))
    return comments


def parse_python(path: Path, rel: str, text: str) -> FileFacts:
    lines = text.splitlines()
    facts = FileFacts(path=rel, language="python", lines=lines)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        # Incomplete buffer (mid-typing): functions are unknowable, but
        # comments are still checkable. Diagnostics degrade, never vanish.
        facts.comments = _py_comments_tolerant(text)
        return facts
    facts.imports = _py_imports(tree)
    facts.from_imports = _py_from_imports(tree)
    # is_method detection: need parent tracking
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            info = _py_func_info(node, text)
            parent = parents.get(id(node))
            info.is_method = isinstance(parent, ast.ClassDef)
            facts.functions.append(info)
    facts.comments = _py_comments(text)
    return facts


def _py_imports(tree: ast.AST) -> dict[str, str]:
    """alias -> top-level module ('' for relative imports). Conservative:
    any binding form counts, at any depth (precision-first: an imported name
    resolves outside snapshot analysis)."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = (a.name or "").split(".")[0]
                out[(a.asname or top).split(".")[0]] = top
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            top = mod.split(".")[0] if mod else ""
            if node.level:
                top = ""  # relative: internal, target unknown statically
            for a in node.names:
                if a.name == "*":
                    continue
                out[a.asname or a.name] = top
    return out


def _py_from_imports(tree: ast.AST) -> list[tuple[str | None, int, list[tuple[str, str | None]], bool, int]]:
    """Structured from-imports: (module, level, [(name, asname)], guarded, lineno).

    Guarded means nested in try/except, a TYPE_CHECKING conditional, or a
    version/platform conditional: compatibility imports that may
    legitimately fail are never flagged.
    """
    out: list[tuple[str | None, int, list[tuple[str, str | None]], bool, int]] = []

    def guarded(node: ast.AST, parents: dict[int, ast.AST]) -> bool:
        seen: set[int] = set()
        cur: ast.AST | None = node
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            parent = parents.get(id(cur))
            if isinstance(parent, (ast.Try, ast.TryStar if hasattr(ast, "TryStar") else ast.Try)):
                return True
            if isinstance(parent, ast.If):
                try:
                    test_src = ast.unparse(parent.test)
                except Exception:
                    test_src = ""
                if ("TYPE_CHECKING" in test_src or "version_info" in test_src
                        or "sys.version" in test_src or "platform" in test_src
                        or "os.name" in test_src):
                    return True
            cur = parent
        return False

    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names = [(a.name, a.asname) for a in node.names]
            out.append((node.module, node.level or 0, names, guarded(node, parents), node.lineno))
    return out


def _py_comments(text: str) -> list[Comment]:
    comments: list[Comment] = []
    try:
        toks = tokenize.generate_tokens(io.StringIO(text).readline)
        for tok in toks:
            if tok.type == tokenize.COMMENT:
                raw = tok.string
                inner = raw[1:]
                if inner.startswith("!"):
                    continue  # shebang-ish / directives kept but not findings; skip
                # strip one leading space conventionally
                if inner.startswith(" "):
                    inner = inner[1:]
                srow, _ = tok.start
                comments.append(Comment(text=inner, raw=raw, line=srow, end_line=srow))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        pass
    return comments


# ---------------------------------------------------------------- JavaScript / TypeScript

_JS_LINE_COMMENT = re.compile(r"//(.*)$")
_JS_BLOCK_START = re.compile(r"/\*")
_JS_BLOCK_END = re.compile(r"\*/")
_JS_FUNC_SIG = re.compile(
    r"(?:function\s+([A-Za-z_$][A-Za-z0-9_$]*)|"
    r"(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?(?:function\b|\(|[^=;]*=>)|"
    r"class\s+([A-Za-z_$][A-Za-z0-9_$]*))"
)
_JS_ARROW_PARAMS = re.compile(r"(?:const|let|var)\s+[A-Za-z_$][A-Za-z0-9_$]*\s*=\s*(?:async\s*)?\(?([^)=;]*?)\)?\s*=>")
_JS_FUNC_PARAMS = re.compile(r"function\s+[A-Za-z_$][A-Za-z0-9_$]*\s*\(([^)]*)\)")
_JS_METHOD_PARAMS = re.compile(r"^\s*(?:async\s+|static\s+|get\s+|set\s+)?[A-Za-z_$][A-Za-z0-9_$]*\s*\(([^)]*)\)")


def _split_params(raw: str) -> list[str]:
    raw = raw.strip()
    if not raw:
        return []
    out: list[str] = []
    depth = 0
    cur = ""
    for ch in raw:
        if ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            name = _clean_param(cur)
            if name:
                out.append(name)
            cur = ""
        else:
            cur += ch
    name = _clean_param(cur)
    if name:
        out.append(name)
    return out


def _clean_param(p: str) -> str:
    p = p.strip()
    if not p:
        return ""
    # strip TS types/defaults: "name: Type = val" -> name ; "{a, b}" -> "" (destructured, keep raw-ish)
    p = p.split("=")[0].strip()
    p = p.split(":")[0].strip()
    p = p.lstrip("...").strip()
    if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", p):
        return ""
    return p


def parse_javascript(path: Path, rel: str, text: str) -> FileFacts:
    lines = text.splitlines()
    facts = FileFacts(path=rel, language="javascript", lines=lines)
    comments, code_mask = _js_comments(lines)
    facts.comments = comments
    facts.functions = _js_functions(lines, comments)
    facts.imports = _js_imports(text)
    facts.js_imports = _js_import_entries(text)
    return facts


_JS_IMPORT_FROM = re.compile(
    r"^\s*import\s+(?:type\s+)?(.*?)\s+from\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
_JS_IMPORT_SIDE = re.compile(r"^\s*import\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
_JS_REQUIRE = re.compile(
    r"(?:const|let|var)\s+(?:(\w+)|[{]([^}]*)[}])\s*=\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)")


def _js_import_entries(text: str) -> list[tuple[str, str, str | None, list[tuple[str, str]], int]]:
    """Structured JS/TS imports: (specifier, kind, default or None,
    [(original, alias)] named, lineno).

    Aliases matter: `import {b as c}` must verify `b` (the export), not
    `c` (the local binding). Kinds: named (may include a default),
    namespace (`* as ns`, binds the module itself), sideeffect
    (`import 'x'`), require (`const X = require('x')`, `{a, b}`).
    """
    out: list[tuple[str, str, str | None, list[tuple[str, str]], int]] = []
    in_template = False
    for idx, line in enumerate(text.splitlines(), start=1):
        # Skip lines inside multi-line template literals: imports quoted in
        # strings are prose, not imports. (Single-line strings are safe:
        # every pattern below is start-anchored.)
        ticks = line.count("`") - line.count("\\`")
        if in_template:
            if ticks % 2 == 1:
                in_template = False
            continue
        elif ticks % 2 == 1:
            in_template = True
            continue
        s = line.strip()
        m = re.match(r"^import\s+(?:type\s+)?(.*?)\s+from\s*['\"]([^'\"]+)['\"]\s*;?\s*$", s)
        if m:
            clause, spec = m.group(1).strip(), m.group(2)
            default: str | None = None
            named: list[tuple[str, str]] = []
            dm = re.match(r"^([A-Za-z_$][A-Za-z0-9_$]*)\s*(,|$)", clause)
            if dm:
                default = dm.group(1)
            brace = re.search(r"\{([^}]*)\}", clause)
            if brace:
                for part in brace.group(1).split(","):
                    part = part.strip()
                    if not part:
                        continue
                    bits = [b.strip() for b in part.split(" as ")]
                    original = bits[0].split(":")[0].strip()
                    alias = bits[-1].split(":")[0].strip()
                    if (re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", original or "")
                            and re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", alias or "")):
                        named.append((original, alias))
            nsm = re.search(r"\*\s+as\s+([A-Za-z_$][A-Za-z0-9_$]*)", clause)
            if nsm:
                out.append((spec, "namespace", nsm.group(1), [], idx))
                continue
            if default is None and not named:
                out.append((spec, "sideeffect", None, [], idx))
            else:
                out.append((spec, "named", default, named, idx))
            continue
        m2 = re.match(r"^import\s*['\"]([^'\"]+)['\"]\s*;?\s*$", s)
        if m2:
            out.append((m2.group(1), "sideeffect", None, [], idx))
            continue
        m3 = _JS_REQUIRE.search(line)
        if m3:
            default, named, spec = m3.group(1), m3.group(2), m3.group(3)
            rnamed: list[tuple[str, str]] = []
            if named:
                for part in named.split(","):
                    bits = [b.strip() for b in part.strip().split(":")]
                    original, alias = bits[0].strip(), bits[-1].strip()
                    if (re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", original or "")
                            and re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", alias or "")):
                        rnamed.append((original, alias))
            out.append((spec, "require", default, rnamed, idx))
    return out


def _js_imports(text: str) -> dict[str, str]:
    """alias -> top-level package ('' for relative/internal specifiers).
    Bare specifiers (react, axios, lodash) are external; ./ ../ / are repo."""
    out: dict[str, str] = {}
    for m in _JS_IMPORT_FROM.finditer(text):
        clause, spec = m.group(1), m.group(2).strip()
        top = "" if spec.startswith((".", "/")) else spec.split("/")[0]
        # default import: `import Foo from 'x'` ; namespace: `* as ns`
        dm = re.match(r"^([A-Za-z_$][A-Za-z0-9_$]*)\s*(,|$)", clause.strip())
        if dm:
            out[dm.group(1)] = top
        nsm = re.search(r"\*\s+as\s+([A-Za-z_$][A-Za-z0-9_$]*)", clause)
        if nsm:
            out[nsm.group(1)] = top
        brace = re.search(r"\{([^}]*)\}", clause)
        if brace:
            for part in brace.group(1).split(","):
                part = part.strip()
                if not part:
                    continue
                bits = [b.strip() for b in part.split(" as ")]
                alias = bits[-1].split(":")[0].strip()
                if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", alias or ""):
                    out[alias] = top
    for m in _JS_REQUIRE.finditer(text):
        default, named, spec = m.group(1), m.group(2), m.group(3).strip()
        top = "" if spec.startswith((".", "/")) else spec.split("/")[0]
        if default:
            out[default] = top
        if named:
            for part in named.split(","):
                part = part.strip()
                if not part:
                    continue
                bits = [b.strip() for b in part.split(":")]
                alias = bits[-1].strip()
                if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", alias or ""):
                    out[alias] = top
    return out


def _js_comments(lines: list[str]) -> tuple[list[Comment], list[bool]]:
    comments: list[Comment] = []
    code_mask = [True] * len(lines)
    i = 0
    n = len(lines)
    in_block = False
    block_start = 0
    block_buf: list[str] = []
    block_raw: list[str] = []
    while i < n:
        line = lines[i]
        if not in_block:
            bs = line.find("/*")
            lc = line.find("//")
            # determine which comes first, accounting for strings is overkill;
            # heuristic: if // appears before /*, treat as line comment
            if bs != -1 and (lc == -1 or bs < lc):
                be = line.find("*/", bs + 2)
                if be != -1:
                    inner = line[bs + 2:be]
                    raw = line[bs:be + 2]
                    comments.append(Comment(text=inner.strip(), raw=raw, line=i + 1, end_line=i + 1, is_block=True))
                    code_mask[i] = False if line[:bs].strip() == "" else code_mask[i]
                    # check for trailing // after block? ignore
                else:
                    in_block = True
                    block_start = i + 1
                    block_buf = [line[bs + 2:]]
                    block_raw = [line[bs:]]
                    code_mask[i] = False if line[:bs].strip() == "" else code_mask[i]
            elif lc != -1:
                # crude string guard: count quotes before lc; if odd, likely inside string -> skip
                prefix = line[:lc]
                if prefix.count('"') % 2 == 1 or prefix.count("'") % 2 == 1 or prefix.count("`") % 2 == 1:
                    pass
                else:
                    inner = line[lc + 2:]
                    if inner.startswith(" "):
                        inner = inner[1:]
                    comments.append(Comment(text=inner, raw=line[lc:], line=i + 1, end_line=i + 1))
                    if prefix.strip() == "":
                        code_mask[i] = False
        else:
            be = line.find("*/")
            if be != -1:
                block_buf.append(line[:be])
                block_raw.append(line[:be + 2])
                full_inner = "\n".join(block_buf)
                # strip leading * per line (JSDoc)
                cleaned = "\n".join(
                    re.sub(r"^\s*\*\s?", "", ln) for ln in full_inner.splitlines()
                ).strip()
                comments.append(Comment(text=cleaned, raw="\n".join(block_raw), line=block_start, end_line=i + 1, is_block=True))
                in_block = False
                block_buf = []
                block_raw = []
            else:
                block_buf.append(line)
                block_raw.append(line)
                code_mask[i] = False
        i += 1
    # mark continuation lines of multi-line blocks as non-code
    for c in comments:
        if c.is_block and c.end_line > c.line:
            for ln in range(c.line, c.end_line + 1):
                if 1 <= ln <= len(code_mask):
                    # only blank-ish lines; keep code lines that share a line with code
                    if lines[ln - 1].strip().startswith(("*", "/*", "//")):
                        code_mask[ln - 1] = False
    return comments, code_mask


def _js_functions(lines: list[str], comments: list[Comment]) -> list[FuncInfo]:
    funcs: list[FuncInfo] = []
    # map JSDoc block end line -> comment
    jsdoc_by_end: dict[int, Comment] = {}
    for c in comments:
        if c.is_block and ("@param" in c.text or "@returns" in c.text or "@return" in c.text or "@throws" in c.text or "@exception" in c.text):
            jsdoc_by_end[c.end_line] = c
    for idx, line in enumerate(lines, start=1):
        name = None
        params: list[str] = []
        m = re.match(r"^\s*(?:export\s+default\s+|export\s+)?(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(([^)]*)\)", line)
        if m:
            name = m.group(1)
            params = _split_params(m.group(2))
        if name is None:
            m2 = re.match(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?\(?([^)=;]*?)\)?\s*=>", line)
            if m2 and ("=>" in line):
                name = m2.group(1)
                params = _split_params(m2.group(2))
        if name is None:
            m3 = re.match(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?function\s*\(([^)]*)\)", line)
            if m3:
                name = m3.group(1)
                params = _split_params(m3.group(2))
        if name is None:
            # multiline signature: `function Name(` without `)` on this line
            m4 = re.match(r"^\s*(?:export\s+default\s+)?(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", line)
            if m4:
                name = m4.group(1)
                pm = re.search(r"\(\s*([^)]*)\)", line)
                params = _split_params(pm.group(1)) if pm else []
        if name is None:
            m5 = re.match(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)\b", line)
            if m5:
                name = m5.group(1)
                params = []
        if name is None and idx > 1:
            m6 = re.match(r"^\s*(?:async\s+|static\s+)?([A-Za-z_$][A-Za-z0-9_$]*)\s*\(([^)]*)\)\s*\{", line)
            if m6:
                # method shape without `function` keyword; conservative fallback
                if m6.group(1) not in {"if", "for", "while", "switch", "catch", "return", "import", "export"}:
                    name = m6.group(1)
                    params = _split_params(m6.group(2))
        if name:
            # find preceding JSDoc within 3 lines
            doc = ""
            doc_line = 0
            for back in range(1, 5):
                c = jsdoc_by_end.get(idx - back)
                if c is not None:
                    # ensure only blank/comment lines between
                    between = lines[idx - back:idx - 1]
                    if all(l.strip() == "" or l.strip().startswith(("*", "/", "@")) for l in between):
                        doc = c.text
                        doc_line = c.line
                    break
            # crude body scan for throw/return (next 60 lines, brace depth)
            has_value_return, raises = _js_body_signals(lines, idx)
            funcs.append(FuncInfo(
                name=name, lineno=idx, end_lineno=idx, args=params,
                docstring=doc, docstring_lineno=doc_line,
                has_value_return=has_value_return, raises=raises,
            ))
    # dedupe by (name, lineno)
    seen: set[tuple[str, int]] = set()
    uniq: list[FuncInfo] = []
    for f in funcs:
        key = (f.name, f.lineno)
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    return uniq


def _js_body_signals(lines: list[str], start: int) -> tuple[bool, list[str]]:
    has_value_return = False
    raises: list[str] = []
    depth = 0
    started = False
    for j in range(start - 1, min(len(lines), start + 80)):
        line = lines[j]
        # strip line comments for signal detection
        code = re.sub(r"//.*$", "", line)
        code = re.sub(r"/\*.*?\*/", "", code)
        depth += code.count("{") - code.count("}")
        if "{" in code:
            started = True
        m = re.search(r"\breturn\b([^;]*)", code)
        if m:
            val = m.group(1).strip().rstrip(";").strip()
            if val and val not in ("", "undefined", "void 0"):
                # bare `return;` vs `return x;`
                has_value_return = True
        for tm in re.finditer(r"\bthrow\s+new\s+([A-Za-z_$][A-Za-z0-9_$.]*)\s*\(", code):
            raises.append(tm.group(1).split(".")[-1])
        for tm in re.finditer(r"\bthrow\s+([A-Za-z_$][A-Za-z0-9_$]*)\b", code):
            if tm.group(1) not in ("new",):
                if tm.group(1) not in raises:
                    raises.append(tm.group(1))
        if started and depth <= 0 and j > start - 1:
            # include one extra line after close? break when balanced
            if depth < 0:
                break
            # wait for the brace depth to settle before closing the scan
            if j > start + 2 and depth == 0:
                break
    return has_value_return, raises


def parse_markdown(path: Path, rel: str, text: str) -> FileFacts:
    # No comment/import/function extraction: the stale-doc-ref checker
    # reads fenced blocks straight from lines. facts carry the text so
    # other checkers (which iterate comments/functions) stay silent.
    return FileFacts(path=rel, language="markdown", lines=text.splitlines())


def parse_file(path: Path, rel: str, text: str) -> FileFacts | None:
    suffix = path.suffix.lower()
    if suffix == ".py":
        return parse_python(path, rel, text)
    if suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"}:
        return parse_javascript(path, rel, text)
    if suffix == ".go":
        return parse_go(path, rel, text)
    if suffix in {".c", ".h"}:
        return parse_c(path, rel, text)
    if suffix in {".md", ".markdown"}:
        return parse_markdown(path, rel, text)
    return None


_C_FUNC = re.compile(
    r"^\s*(?:static\s+|inline\s+|extern\s+)?"
    r"(?:[A-Za-z_][A-Za-z0-9_\s\*]*?\b)?([A-Za-z_][A-Za-z0-9_]*)\s*\(")
# NOTE: the type prefix is optional so single-token declarators
# (`name(...)` after a lone return type) match. Callers must require a
# non-empty prefix or pending-type state; a bare `foo(x);` call matches
# this pattern but is not a definition.
_C_FUNC_EXCLUDE = {
    "if", "for", "while", "switch", "return", "sizeof", "defined",
    "do", "else", "case", "goto",
}
_C_TYPE = re.compile(
    r"^\s*(?:typedef\s+)?(?:struct|enum|union)\s+([A-Za-z_][A-Za-z0-9_]*)")
_C_DEFINE = re.compile(r"^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)")
_C_INCLUDE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]')
# Function-pointer members (`int (*cb)(...)`, RedisModule API struct).
_C_FPTR = re.compile(r"\(\*([A-Za-z_][A-Za-z0-9_]*)\)\s*\(")
# Type keywords that the function pattern can mistake for a name
# (`REDISMODULE_API void (*cb)(...)` must yield `cb`, never `void`).
_C_RESERVED = frozenset({
    "void", "char", "short", "int", "long", "float", "double", "signed",
    "unsigned", "const", "static", "inline", "extern", "typedef", "struct",
    "enum", "union", "sizeof",
})
# A lone return type (`static void` with nothing else) means the declarator
# (and the defined name) sits on the next line. Ubiquitous in C codebases.
_C_LONE_TYPE = re.compile(
    r"^\s*(?:static\s+|inline\s+|extern\s+)?(?:const\s+)?[A-Za-z_][A-Za-z0-9_]*"
    r"(?:\s*\*+|\s+[A-Za-z_][A-Za-z0-9_\s\*]*?)?\s*$")


def c_top_level_names(text: str) -> set[str]:
    """Defined names (functions incl. split declarations, types, macros).

    Single source of truth shared by the parser and the repo index, so the
    two can never disagree about what counts as defined.
    """
    names: set[str] = set()
    lines = text.splitlines()
    pending_type = False
    for pos, line in enumerate(lines):
        s = line.strip()
        if s.startswith("#"):
            d = _C_DEFINE.match(line)
            if d:
                names.add(d.group(1))
            pending_type = False
            continue
        for fm in _C_FPTR.finditer(line):
            names.add(fm.group(1))
        m = _C_FUNC.match(line)
        if m and m.group(1) not in _C_FUNC_EXCLUDE and m.group(1) not in _C_RESERVED:
            prefix = re.sub(r"^\s*(?:static|inline|extern)\s+", "", line[:m.start(1)]).strip()
            if prefix and re.search(r"[A-Za-z_]", prefix):
                names.add(m.group(1))
                pending_type = False
                continue
            if pending_type:
                names.add(m.group(1))
                pending_type = False
                continue
            # K&R style: `name(args)` with no return type, brace either on
            # this line or the next non-empty one. A brace (and no `;`)
            # proves it is not a call statement.
            rest = line.rstrip()
            nxt = ""
            k = pos + 1
            while k < len(lines) and not lines[k].strip():
                k += 1
            if k < len(lines):
                nxt = lines[k].strip()
            if rest.endswith("{") or nxt == "{":
                names.add(m.group(1))
                pending_type = False
                continue
        t = _C_TYPE.match(line)
        if t:
            names.add(t.group(1))
            pending_type = False
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "/*", "*")):
            pending_type = False
            continue
        pending_type = bool(_C_LONE_TYPE.match(line)) and "(" not in line and ";" not in line and "{" not in line and "}" not in line
    return names


def _c_imports(text: str) -> dict[str, str]:
    """header base -> package path ('' for quoted local includes)."""
    out: dict[str, str] = {}
    for m in _C_INCLUDE.finditer(text):
        spec = m.group(1).strip()
        base = spec.split("/")[-1].split(".")[0]
        if not base:
            continue
        is_local = bool(re.search(r'#\s*include\s*"', m.group(0)))
        out[base] = "" if is_local else spec.split("/")[0]
    return out


def parse_c(path: Path, rel: str, text: str) -> FileFacts:
    lines = text.splitlines()
    facts = FileFacts(path=rel, language="c", lines=lines)
    facts.comments, _ = _js_comments(lines)
    known = c_top_level_names(text)
    funcs: list[FuncInfo] = []
    for idx, line in enumerate(lines, start=1):
        m = _C_FUNC.match(line)
        if m and m.group(1) in known:
            funcs.append(FuncInfo(name=m.group(1), lineno=idx, end_lineno=idx, args=[]))
            continue
        t = _C_TYPE.match(line)
        if t and t.group(1) in known:
            funcs.append(FuncInfo(name=t.group(1), lineno=idx, end_lineno=idx, args=[]))
            continue
        d = _C_DEFINE.match(line)
        if d and d.group(1) in known:
            funcs.append(FuncInfo(name=d.group(1), lineno=idx, end_lineno=idx, args=[]))
    facts.functions = funcs
    facts.imports = _c_imports(text)
    return facts


_GO_FUNC = re.compile(r"^\s*func\s+(?:\([^)]*\)\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_GO_TYPE = re.compile(r"^\s*type\s+([A-Za-z_][A-Za-z0-9_]*)\b")
_GO_IMPORT_BLOCK = re.compile(r"^\s*import\s*\(", re.MULTILINE)
_GO_IMPORT_LINE = re.compile(r'^\s*import\s+(?:([A-Za-z_\.][A-Za-z0-9_]*)\s+)?["`]([^"`]+)["`]')
_GO_IMPORT_ENTRY = re.compile(r'^\s*(?:([A-Za-z_\.][A-Za-z0-9_]*)\s+)?["`]([^"`]+)["`]\s*$')


def _go_imports(text: str) -> dict[str, str]:
    """alias -> top path segment ('' for relative-ish/internal)."""
    out: dict[str, str] = {}
    for m in _GO_IMPORT_LINE.finditer(text):
        alias, spec = m.group(1), m.group(2)
        if spec.startswith((".", "/")):
            top = ""
        else:
            top = spec.split("/")[0]
        if alias in (None, "", "_", "."):
            if alias in ("_", "."):
                continue
            # `import "fmt"` binds package name = last segment
            out[spec.rstrip("/").split("/")[-1]] = top
        else:
            out[alias] = top
    in_block = False
    for line in text.splitlines():
        if not in_block:
            if _GO_IMPORT_BLOCK.match(line):
                in_block = True
            continue
        if re.match(r"^\s*\)", line):
            in_block = False
            continue
        m = _GO_IMPORT_ENTRY.match(line)
        if not m:
            continue
        alias, spec = m.group(1), m.group(2)
        top = "" if spec.startswith((".", "/")) else spec.split("/")[0]
        if alias in (None, "", "_", "."):
            if alias in ("_", "."):
                continue
            out[spec.rstrip("/").split("/")[-1]] = top
        else:
            out[alias] = top
    return out


def parse_go(path: Path, rel: str, text: str) -> FileFacts:
    lines = text.splitlines()
    facts = FileFacts(path=rel, language="go", lines=lines)
    comments: list[Comment] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("//"):
            inner = stripped[2:]
            if inner.startswith(" "):
                inner = inner[1:]
            comments.append(Comment(text=inner, raw=stripped, line=i + 1, end_line=i + 1))
        elif "/*" in line:
            bs = line.find("/*")
            be = line.find("*/", bs + 2)
            prefix = line[:bs]
            if be != -1:
                if prefix.strip() == "":
                    comments.append(Comment(text=line[bs + 2:be].strip(), raw=line[bs:be + 2],
                                           line=i + 1, end_line=i + 1))
            else:
                buf = [line[bs + 2:]]
                j = i + 1
                while j < n and "*/" not in lines[j]:
                    buf.append(lines[j])
                    j += 1
                if j < n:
                    buf.append(lines[j][:lines[j].find("*/")])
                    if prefix.strip() == "":
                        comments.append(Comment(text="\n".join(buf).strip(),
                                               raw="\n".join(buf), line=i + 1, end_line=j + 1))
                    i = j
        i += 1
    facts.comments = comments
    funcs: list[FuncInfo] = []
    for idx, line in enumerate(lines, start=1):
        m = _GO_FUNC.match(line)
        if m and m.group(1) not in {"if", "for", "switch", "select", "range"}:
            # preceding // doc block within 2 lines attaches as docstring
            doc_lines: list[str] = []
            k = idx - 2
            while k >= 0 and lines[k].strip().startswith("//"):
                doc_lines.append(lines[k].strip()[2:].strip())
                k -= 1
            doc_lines.reverse()
            # crude signature params for future use; body signals skipped:
            # Go checkers use symbol/file/anchor/number only.
            funcs.append(FuncInfo(name=m.group(1), lineno=idx, end_lineno=idx,
                                  args=[], docstring="\n".join(doc_lines),
                                  docstring_lineno=k + 2 if doc_lines else 0))
            continue
        t = _GO_TYPE.match(line)
        if t:
            funcs.append(FuncInfo(name=t.group(1), lineno=idx, end_lineno=idx, args=[]))
    facts.functions = funcs
    facts.imports = _go_imports(text)
    return facts
