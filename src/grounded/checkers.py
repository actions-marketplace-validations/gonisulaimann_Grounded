"""Deterministic reference checks for code comments, v2.

Not covered here, by design: docstring contracts (params, returns, raises)
and commented-out code. Use darglint or pydoclint (Python),
eslint-plugin-jsdoc (JS/TS), and Ruff ERA001 for those; each was compared
head-to-head and is more precise on its surface (see REMOVED_CHECKERS).

Covered here (no exact incumbent found):
- stale-symbol-ref: comment names a call that resolves nowhere
  (not defined, imported, or used in-file; not stdlib/builtin/keyword).
- stale-file-ref: comment claims a path inside this repo's tree that
  does not exist (namespace- and placeholder-aware).
- number-drift: magic number in a comment disagrees with adjacent code.
- fragile-anchor: line anchors and untracked workaround markers.

A finding is emitted only with positive evidence of contradiction.
"""
from __future__ import annotations

import ast
import difflib
import re
import sys

from .models import Comment, FileFacts, Finding
from .repo_index import RepoIndex

# ---------------------------------------------------------------- shared

PYTHON_BUILTINS = {
    "print", "len", "range", "str", "int", "float", "bool", "list", "dict", "set",
    "tuple", "super", "isinstance", "issubclass", "hasattr", "getattr", "setattr",
    "open", "enumerate", "zip", "map", "filter", "sorted", "reversed", "sum",
    "min", "max", "abs", "round", "pow", "divmod", "repr", "type", "id",
    "input", "iter", "next", "callable", "classmethod", "staticmethod",
    "property", "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
    "AttributeError", "RuntimeError", "NotImplementedError", "StopIteration",
    "AssertionError", "IOError", "OSError", "print_function",
    "object", "file",
    # Python 2 historical builtins still referenced in old comments/docs
    "execfile", "unicode", "long", "xrange", "raw_input", "basestring",
    "reduce", "apply", "intern", "coerce",
}

JS_GLOBALS = {
    "console", "log", "warn", "error", "info", "debug", "assert",
    "fetch", "setTimeout", "setInterval", "clearTimeout", "clearInterval",
    "Promise", "Array", "Object", "String", "Number", "Boolean", "JSON",
    "Math", "Date", "RegExp", "Error", "TypeError", "RangeError",
    "require", "module", "exports", "process", "setImmediate",
    "describe", "it", "test", "expect", "beforeEach", "afterEach",
    "useState", "useEffect", "useRef", "useMemo", "useCallback",
}

# Language keywords must never be mistaken for symbol references,
# even when backticked in prose ("without `function` keyword").
KEYWORDS = {
    "function", "return", "if", "else", "elif", "for", "while", "class",
    "const", "let", "var", "import", "export", "from", "def", "pass",
    "break", "continue", "try", "except", "finally", "with", "as",
    "await", "async", "yield", "new", "this", "self", "true", "false",
    "none", "null", "undefined", "type", "interface", "extends",
}

COMMON_ENGLISH_FUNCWORDS = {
    "example", "todo", "fixme", "note", "warning", "error", "info",
    "something", "nothing", "anything", "everything",
}

GO_BUILTINS = {
    "append", "cap", "clear", "close", "complex", "copy", "delete",
    "imag", "len", "make", "max", "min", "new", "panic", "print",
    "println", "real", "recover", "error", "string", "int", "int8",
    "int16", "int32", "int64", "uint", "uint8", "uint16", "uint32",
    "uint64", "uintptr", "float32", "float64", "complex64", "complex128",
    "bool", "byte", "rune", "any", "comparable", "true", "false", "nil",
    "iota",
}

GO_KEYWORDS = {
    "break", "case", "chan", "const", "continue", "default", "defer",
    "else", "fallthrough", "for", "func", "go", "goto", "if", "import",
    "interface", "map", "package", "range", "return", "select", "struct",
    "switch", "type", "var",
}

# C standard library functions commonly named in comments without includes
# (printf, malloc, ...). Anything else resolves via #include maps or defs.
C_STDLIB_FUNCS = {
    "printf", "fprintf", "sprintf", "snprintf", "scanf", "puts", "putchar",
    "getchar", "malloc", "calloc", "realloc", "free", "memcpy", "memmove",
    "memset", "memcmp", "strlen", "strcpy", "strncpy", "strcmp", "strncmp",
    "strcat", "strchr", "strstr", "strtok", "atoi", "exit", "abort", "qsort",
    "fopen", "fclose", "fread", "fwrite", "fseek", "ftell", "feof", "perror",
    "assert", "sizeof",
    "isspace", "isdigit", "isalpha", "isalnum", "isxdigit", "isprint",
    "ispunct", "iscntrl", "isgraph", "islower", "isupper", "toupper", "tolower",
    "strpbrk", "strspn", "strcspn", "strerror", "strdup", "strndup",
    "bsearch", "wcspbrk", "wcslen", "va_start", "va_end", "va_arg",
    "offsetof",
    # POSIX / sockets / pthreads, the most-referenced C APIs in comments
    "fork", "execve", "execvp", "waitpid", "pipe", "dup", "dup2", "close",
    "read", "write", "open", "lseek", "fsync", "stat", "fstat", "unlink",
    "link", "symlink", "readlink", "truncate", "ftruncate", "chmod", "chown",
    "getpid", "getuid", "sleep", "usleep", "nanosleep", "mmap", "munmap",
    "socket", "bind", "listen", "accept", "connect", "send", "recv",
    "sendto", "recvfrom", "setsockopt", "getsockopt", "getsockname",
    "getpeername", "htonl", "htons", "ntohl", "ntohs", "inet_ntoa",
    "inet_pton", "getaddrinfo", "freeaddrinfo", "select", "poll", "epoll",
    "pthread_create", "pthread_join", "pthread_mutex_lock",
    "pthread_mutex_unlock", "dlopen", "dlsym", "rand", "srand", "time",
    "gettimeofday", "localtime", "gmtime", "signal", "kill", "madvise",
    "strtol", "strtoul", "strtoll", "strtoull", "strtod", "strtof",
    "atoll", "atof",
}

C_KEYWORDS = {
    "auto", "break", "case", "char", "const", "continue", "default", "do",
    "double", "else", "enum", "extern", "float", "for", "goto", "if",
    "inline", "int", "long", "register", "restrict", "return", "short",
    "signed", "sizeof", "static", "struct", "switch", "typedef", "union",
    "unsigned", "void", "volatile", "while", "define", "include", "ifdef",
    "ifndef", "endif", "pragma",
}


def _is_reserved(base: str, language: str) -> bool:
    """Builtins, keywords, and prose words: never symbol references."""
    if base in PYTHON_BUILTINS or base in JS_GLOBALS or base in KEYWORDS:
        return True
    if base.lower() in COMMON_ENGLISH_FUNCWORDS:
        return True
    if base.lower() in KEYWORDS:
        return True
    if base.lower() in {"true", "false", "none", "null", "undefined", "nil"}:
        return True
    if language == "go" and (base in GO_BUILTINS or base in GO_KEYWORDS):
        return True
    if language == "c" and (base in C_STDLIB_FUNCS or base in C_KEYWORDS):
        return True
    return False

PLACEHOLDER_PATH_HINTS = {"example", "examples", "path", "to", "foo", "bar", "baz",
    "placeholder", "sample", "demo", "<", ">", "...", "xxx",
    "myapp", "mysite", "app_label", "yourproject", "yourdomain", "sitename"}

REFERENCE_VERBS = re.compile(
    r"\b(calls?|invokes?|uses?|using|see|refers?\s+to|delegates?\s+to|wraps?|handled?\s+by|defined\s+in|implemented\s+in)\b",
    re.IGNORECASE,
)

_SYMBOL_CALL = re.compile(r"(?<![A-Za-z0-9_$.])([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\(\)")
_BACKTICK_SYMBOL = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]*(?:\(\))?)`")
# v2: explicit alphabetic extension list. A generic `[A-Za-z0-9]{1,5}` class
# matched protocol versions (`HTTP/1.1`) and version strings as "files".
_FILE_EXT = (r"py|pyi|js|jsx|ts|tsx|mjs|cjs|mts|cts|json|yaml|yml|toml|md|rst|"
             r"txt|css|html|htm|sh|c|h|cpp|hpp|cc|go|rs|java|po|pot|sql|xml|"
             r"ini|cfg|vue|svelte|rb|php|swift|kt|graphql|gql|proto")
_FILE_REF = re.compile(
    r"(?<![A-Za-z0-9_/:])"
    r"([A-Za-z0-9_][A-Za-z0-9_\-\.]*(?:/[A-Za-z0-9_\-\.]+)+\.(" + _FILE_EXT + r"))"
    r"(?![A-Za-z0-9_])")
_LINE_ANCHOR = re.compile(r"\b[Ll]ines?\s+\d+(?:\s*[-–:]\s*\d+)?\b")
_SEE_ABOVE_BELOW = re.compile(r"\bsee\s+(above|below)\b", re.IGNORECASE)
_TEMPORAL = re.compile(r"\b(temporarily|temporary\s+fix|for\s+now|currently|at\s+the\s+moment)\b", re.IGNORECASE)
_HACK_UPPER = re.compile(r"\b(HACK|XXX|FIXME)\b")  # case-sensitive by convention; "Xxx" prose must not match
_WORKAROUND_WORD = re.compile(r"\b(workaround|work-around|kludge|magic\s+number)\b", re.IGNORECASE)
_TICKET = re.compile(r"(#[0-9]{1,6}\b|https?://\S+|GH-\d+|JIRA-[A-Z]+-\d+|[A-Z]{2,}-\d+)", re.IGNORECASE)

# v2: negated/alternative contexts discuss external systems, not repo
# existence ("Don't use RANDOM()", "byref() calls are not needed",
# "VALUES() is not supported"). Narrow on purpose (necessity/support
# negations only): a generic "does not <verb>" ("does not cache") must
# NOT suppress, or real rename-refs die with it.
_NEGATED = re.compile(
    r"\b(don'?t|doesn'?t\s+(?:use|need|support|exist|matter)|isn'?t|"
    r"aren'?t|wasn'?t|weren'?t|not\s+(?:needed|supported|required|used|"
    r"necessary|available)|never|no longer|instead|avoid|avoids|"
    r"unsupported)\b", re.IGNORECASE)

# v2: illustrative-example markers. Docs constantly invent paths
# ("For example ... ``django/templatetags/news/photos.py``"); a file ref
# within ~120 chars after such a marker is an example, not a claim.
_ILLUSTRATIVE = re.compile(
    r"\b(for example|for instance|e\.g\.|such as|suppose|imagine|example)\b",
    re.IGNORECASE)


def _workaround_match(text: str):
    m = _HACK_UPPER.search(text)
    if m:
        return m
    return _WORKAROUND_WORD.search(text)


def _strip_strings(text: str) -> str:
    return re.sub(r"(['\"`]).*?\1", r"\1\1", text)


# v2: import- and scope-awareness for stale-symbol-ref.
_STDLIB_MODULES = set(getattr(sys, "stdlib_module_names", ())) | {
    "os", "sys", "re", "json", "tarfile", "http", "urllib", "pathlib",
    "typing", "collections", "functools", "itertools", "io", "ast",
}

# Ubiquitous implicit dunders: never worth flagging.
_DUNDER_OK = {
    "__name__", "__main__", "__doc__", "__dict__", "__class__",
    "__module__", "__slots__", "__weakref__",
}


def _is_dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def _stdlib_class(root: str) -> bool:
    """Capitalized root whose lowercase is a stdlib module (Tarfile against
    tarfile; repo definitions still take precedence via the index check)."""
    return bool(root) and root[0].isupper() and root.lower() in _STDLIB_MODULES


# Sphinx field-list lines and JSDoc tag lines are *contracts* (the
# incumbents' surface: darglint / eslint-plugin-jsdoc), not references.
# Scanning them for symbol refs yields field-name FPs (:param cookiejar:).
_SPHINX_FIELD_LINE = re.compile(
    r"^\s*:(param|arg|argument|type|return|rtype|returns|raises|except|"
    r"var|ivar|cvar|meta|keyword|key)\b")
_JSDOC_TAG_LINE = re.compile(
    r"^\s*\*?\s*@?(param|arg|argument|returns?|throws?|exception|type|"
    r"typedef|property|prop|template|yields?)\b")


def _scrub_docstring(doc: str, language: str) -> str:
    # Go doc comments carry no contract tags. C uses Doxygen, whose
    # @param/@return lines are contracts like JSDoc.
    if not doc or language == "go":
        return doc
    pat = _JSDOC_TAG_LINE if language in ("javascript", "c") else _SPHINX_FIELD_LINE
    kept = [ln for ln in doc.splitlines() if not pat.match(ln)]
    return "\n".join(kept)


def _comment_lines(facts: FileFacts) -> dict[int, str]:
    """Map of line number to comment text (multi-line blocks expanded)."""
    by_line: dict[int, str] = {}
    for c in facts.comments:
        for ln in range(c.line, c.end_line + 1):
            by_line.setdefault(ln, c.text)
    return by_line


def _nearby_ticket(facts: FileFacts, line: int, end_line: int, by_line: dict[int, str] | None = None) -> bool:
    """Ticket links often sit next to the marker ("See <url>" below a
    "Temporarily ..." comment; "#10355" beside a history note). Markers
    with a ticket within two lines point outside on purpose."""
    if by_line is None:
        by_line = _comment_lines(facts)
    for ln in range(line - 2, end_line + 3):
        if _TICKET.search(by_line.get(ln, "")):
            return True
    return False


def _code_text(facts: FileFacts) -> str:
    """File text minus full-line comments: cheap same-file identifier proxy.

    If a name appears in code (param, local, attribute, import), a comment
    mentioning it is not a dangling reference. Approximate but sound in one
    direction: it only ever suppresses, and trailing-code lines are kept.
    """
    skip: set[int] = set()
    for c in facts.comments:
        for ln in range(c.line, c.end_line + 1):
            if 1 <= ln <= len(facts.lines):
                s = facts.lines[ln - 1].strip()
                if facts.language == "python":
                    if s.startswith("#"):
                        skip.add(ln)
                else:
                    if s.startswith(("//", "/*", "*", "*/")):
                        skip.add(ln)
    return "\n".join(
        ln for i, ln in enumerate(facts.lines, start=1) if i not in skip)


def _appears_in_code(name: str, code: str) -> bool:
    return re.search(r"\b" + re.escape(name) + r"\b", code) is not None


def _appears_as_suffix(base: str, code: str, minimum: int = 6) -> bool:
    """A longer code identifier ends with the claimed name (`je_` wrappers,
    `XXH3_64bits_withSecret` variant families). Such comments name a member
    of a family, not a standalone function. Suffix-only on purpose: prefix
    extensions (`foo` -> `foo_v2`) are the classic rename shape and must
    still fire."""
    if len(base) < minimum:
        return False
    for tok in set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", code)):
        if len(tok) > len(base) and tok.endswith(base):
            return True
    return False


def _suggest(base: str, index: RepoIndex, limit: int = 2) -> str:
    try:
        near = difflib.get_close_matches(
            base, sorted(index.all_symbols), n=limit, cutoff=0.8)
    except Exception:
        near = []
    if near:
        return " Did you mean: " + ", ".join(f"`{n}`" + "()" for n in near)
    return ""


# ---------------------------------------------------------------- suppression machinery

def _looks_like_code_block(body: str, language: str) -> tuple[bool, float]:
    """Kept as suppression machinery: commented-out code blocks must not
    spawn cascading symbol/file/number findings (their contents are code,
    not references). No longer emits findings itself (see REMOVED_CHECKERS:
    use Ruff ERA001)."""
    s = body.strip()
    if not s:
        return False, 0.0
    if language == "python":
        first = s.splitlines()[0].strip()
        if re.match(r"^(type|noqa|pylint|flake8|ruff|pragma|fmt|isort|coding|eslint|ts-|@)", first, re.IGNORECASE):
            return False, 0.0
        if re.match(r"^#?\s*(noqa|type:\s*ignore|pylint:\s*disable|eslint-disable)", s, re.IGNORECASE):
            return False, 0.0
        try:
            tree = ast.parse(s)
        except (SyntaxError, ValueError):
            return False, 0.0
        code_nodes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import,
                      ast.ImportFrom, ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Return,
                      ast.If, ast.For, ast.While, ast.With, ast.Try, ast.Raise, ast.Assert,
                      ast.Expr)
        if not any(isinstance(n, code_nodes) for n in ast.walk(tree)):
            return False, 0.0
        code_tokens = len(re.findall(r"[(){}\[\];=:.]|^(def|class|import|from|return|if|for|while|with|try|raise|assert)\b", s, re.MULTILINE))
        words = len(re.findall(r"[A-Za-z]+", s))
        density = code_tokens / max(1, words)
        if density < 0.15 and len(s.splitlines()) < 4:
            return False, density
        return True, min(0.95, 0.6 + density)
    else:
        # Suppression-only use (v2): the bar here is intentionally high. This
        # gate only decides whether a comment block reads as code (which
        # suppresses reference claims). Over-firing hides real findings;
        # under-firing only risks extra claims. Prose with a couple of
        # keywords or parens must not qualify.
        if re.match(r"^(eslint|ts-ignore|ts-expect|@|prettier|stylelint)", s.strip(), re.IGNORECASE):
            return False, 0.0
        punct = len(re.findall(r"[;{}()\[\]=><&|]", s))
        chars = max(1, len(re.sub(r"\s", "", s)))
        density = punct / chars
        keywords = len(re.findall(r"\b(const|let|var|function|return|import|export|if|for|while|class|new|await|require)\b", s))
        if density >= 0.12 or (keywords >= 3 and density >= 0.05):
            return True, min(0.9, 0.55 + density + 0.05 * keywords)
        return False, density


def _commented_code_line_set(facts: FileFacts) -> set[int]:
    """Lines belonging to commented-out code blocks (suppression only)."""
    covered: set[int] = set()
    comments = facts.comments
    groups: list[list[Comment]] = []
    cur: list[Comment] = []
    prev_end = -10
    for c in comments:
        if c.is_block:
            if cur:
                groups.append(cur)
                cur = []
            if c.end_line - c.line + 1 >= 3 and "@param" not in c.text:
                ok, _ = _looks_like_code_block(c.text, facts.language)
                if ok and not re.search(r"copyright|license|spdx|warranty", c.text, re.IGNORECASE):
                    for ln in range(c.line, c.end_line + 1):
                        covered.add(ln)
            prev_end = c.end_line
            continue
        if c.line == prev_end + 1:
            cur.append(c)
        else:
            if cur:
                groups.append(cur)
            cur = [c]
        prev_end = c.end_line
    if cur:
        groups.append(cur)
    for g in groups:
        if len(g) < 3:
            continue
        body = "\n".join(c.text for c in g)
        body_lines = [ln for ln in body.splitlines() if not re.fullmatch(r"\s*[=#\-*_~]{4,}\s*", ln)]
        if len(body_lines) < 2:
            continue
        ok, _ = _looks_like_code_block("\n".join(body_lines), facts.language)
        if ok:
            for c in g:
                covered.add(c.line)
    return covered


# ---------------------------------------------------------------- checkers

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
    for f in facts.functions:
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
            if _is_reserved(base, facts.language):
                continue
            # v2: non-call backticked names are fields/attrs/prose, except
            # dunders: dunder names are almost always real protocol
            # methods, so a dunder with no definition anywhere is worth
            # flagging (typo class).
            if not is_call:
                if not _is_dunder(base) or base in _DUNDER_OK:
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
            if _is_reserved(base, facts.language):
                continue
            if f"`{full}()`" in text or f"`{base}()`" in text:
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


def _in_repo_scope(ref: str, index: RepoIndex) -> bool:
    """v2: only judge claims about THIS repo's tree.

    Measured failure: template namespaces (flatpages/default.html),
    external-project sources (Modules/_sqlite/connection.c), dependency
    modules (MySQLdb/cursors.py), i18n patterns (en/LC_MESSAGES/x.po) were
    all flagged as "missing files". Rule: judge a ref only when it is
    explicitly relative (./ ../ /) or its first segment is a repo
    top-level name. Everything else is an external/namespace reference the
    snapshot cannot decide.
    """
    r = ref.strip()
    if r.startswith(("./", "../", "/")):
        return True
    first = r.lstrip("./").split("/")[0]
    return first in index.top_names


def _resolve_py_target(index: RepoIndex, claimer: str, module: str | None, level: int) -> list[str] | None:
    """Candidate module rel paths for an import, or None if unresolvable
    (stdlib, third-party, namespace packages: outside snapshot analysis).

    Relative (level>0) walks up from the claiming file. Absolute requires
    the top segment to be a repo root entry.
    """
    if level:
        base = _resolve_py_base(claimer, level)
        if not base and level - 1 > len(claimer.split("/")[:-1]):
            return None
        if module:
            base = base + module.split(".")
        prefix = "/".join(base)
    else:
        if not module:
            return None
        segs = module.split(".")
        if segs[0] not in index.top_names:
            return None
        prefix = "/".join(segs)
    if not prefix:
        return None
    return [prefix + ".py", prefix + "/__init__.py"]


def _effective_symbols(index: RepoIndex, rel: str, depth: int = 0,
                       seen: frozenset[str] | None = None) -> set[str]:
    """Names a module file provides: defs, assignments, imports, plus one
    star-import hop (`from .models import *` re-exports everything). Depth
    capped with a seen-set; __init__ chains resolve in one hop in practice.
    """
    seen = seen or frozenset()
    if rel in seen or depth > 2:
        return set()
    seen = seen | {rel}
    out = set(index.file_symbols.get(rel, set())) | set(index.file_imports.get(rel, set()))
    for module, level in index.file_stars.get(rel, []):
        targets = _resolve_py_target(index, rel, module, level)
        if targets is None:
            continue
        for t in targets:
            if t in index.rel_paths:
                out |= _effective_symbols(index, t, depth + 1, seen)
    return out


def check_stale_import(facts: FileFacts, index: RepoIndex) -> list[Finding]:
    """Python resolvable-import verification (v0.8).

    `from M import N` / `import M` where M resolves to a repo file: the
    module must exist, and N must be defined there, re-exported there, or
    itself a submodule (`from pkg import submod` is ubiquitous and valid).
    Guarded imports (try/except, TYPE_CHECKING, version/platform checks)
    and unresolvable modules (stdlib, deps) are never flagged. An
    unresolvable-at-runtime import is an ImportError, so these are lies.
    """
    if facts.language != "python":
        return []
    findings: list[Finding] = []
    for module, level, names, is_guarded, lineno in facts.from_imports:
        if is_guarded:
            continue
        targets = _resolve_py_target(index, facts.path, module, level)
        if targets is None:
            continue
        existing = [t for t in targets if t in index.rel_paths]
        for name, _asname in names:
            if name == "*":
                continue
            if name.startswith("__") and name.endswith("__"):
                continue  # import system provides dunders (__file__, ...)
            if module is None:
                # `from . import sub`: the name may be a submodule file, or
                # a name defined/re-exported by the package __init__.
                base = "/".join(_resolve_py_base(facts.path, level))
                pkg_init = (base + "/__init__.py") if base else "__init__.py"
                if (name in index.file_symbols.get(pkg_init, set())
                        or name in index.file_imports.get(pkg_init, set())):
                    continue
                subs = ([base + "/" + name + ".py", base + "/" + name + "/__init__.py"]
                        if base else [name + ".py", name + "/__init__.py"])
                if any(s in index.rel_paths for s in subs):
                    continue
                findings.append(Finding(
                    path=facts.path, line=lineno, end_line=lineno,
                    checker="stale-import", severity="lie",
                    title=f"`from . import {name}` but no such submodule exists",
                    claim=f"from {'.' * level} import {name}",
                    evidence="No matching submodule file exists in this repo.",
                    fix="Fix the submodule path or remove the import.",
                    confidence=0.85,
                ))
                break
            if not existing:
                findings.append(Finding(
                    path=facts.path, line=lineno, end_line=lineno,
                    checker="stale-import", severity="lie",
                    title=f"`from {module}` imports from a module that does not exist",
                    claim=f"from {'.' * level}{module or ''} import {name}",
                    evidence="No such module file exists in this repo.",
                    fix="Fix the module path or remove the import.",
                    confidence=0.85,
                ))
                break
            provided = any(name in _effective_symbols(index, t) for t in existing)
            # PEP 562: a module-level __getattr__ means any name may resolve.
            dynamic = any("__getattr__" in index.file_symbols.get(t, set()) for t in existing)
            home = existing[0].rsplit("/", 1)[0] if "/" in existing[0] else ""
            submod = (home + "/" + name + ".py") if home else (name + ".py")
            subpkg = (home + "/" + name + "/__init__.py") if home else (name + "/__init__.py")
            if provided or dynamic or submod in index.rel_paths or subpkg in index.rel_paths:
                continue
            findings.append(Finding(
                path=facts.path, line=lineno, end_line=lineno,
                checker="stale-import", severity="lie",
                title=f"`{name}` imported from `{existing[0]}` but never defined there",
                claim=f"from {'.' * level}{module or ''} import {name}",
                evidence=f"`{existing[0]}` exists but defines no `{name}`.",
                fix=f"Check for a rename in `{existing[0]}` (or a moved submodule).",
                confidence=0.8,
            ))
    return _dedupe(findings)


def _resolve_py_base(claimer: str, level: int) -> list[str]:
    parts = claimer.split("/")[:-1]
    if level - 1 > len(parts):
        return []
    return parts[:len(parts) - (level - 1)]


def check_stale_file(facts: FileFacts, index: RepoIndex) -> list[Finding]:
    findings: list[Finding] = []
    dead = _commented_code_line_set(facts)

    def _scan(text: str, line: int, end: int, kind: str) -> None:
        if not text.strip():
            return
        text = re.sub(r"https?://\S+", "", text)
        # Ticket-anchored history notes ("used to be in X.py ... See #10355")
        # point outside on purpose, like ticketed symbol claims. The ticket
        # may sit on a neighboring comment line.
        if _TICKET.search(text) or _nearby_ticket(facts, line, end):
            return
        for m in _FILE_REF.finditer(text):
            ref = m.group(1)
            segs = [s.lower() for s in re.split(r"[/.]", ref)]
            if any(h in segs for h in PLACEHOLDER_PATH_HINTS):
                continue
            if "://" in ref:
                continue
            if not _in_repo_scope(ref, index):
                continue
            # v2: illustrative examples invent paths ("For example ...
            # ``django/templatetags/news/photos.py``"); a file ref
            # within ~120 chars after such a marker is an example, not a claim.
            if _ILLUSTRATIVE.search(text[max(0, m.start() - 120):m.start()]):
                continue
            if index.has_exact_path(ref):
                continue
            same = index.same_named(ref)
            detail = ""
            if same:
                shown = ", ".join(f"`{s}`" for s in same[:3])
                more = f" (+{len(same) - 3} more)" if len(same) > 3 else ""
                detail = f" Same-named files exist: {shown}{more}."
            findings.append(Finding(
                path=facts.path, line=line, end_line=end,
                checker="stale-file-ref", severity="lie",
                title=f"{kind} references missing file `{ref}`",
                claim=ref,
                evidence=f"`{ref}` claims a path inside this repo "
                         f"(first segment matches the repo tree), but no such file exists.{detail}",
                fix="Update the path or remove the reference. List nearby files to find the rename.",
                confidence=0.85,
            ))

    for c in facts.comments:
        if c.line in dead and c.end_line in dead:
            continue
        _scan(c.text, c.line, c.end_line, "Comment")
    # docstrings too
    for f in facts.functions:
        if f.docstring:
            _scan(f.docstring, f.docstring_lineno or f.lineno,
                  f.docstring_lineno or f.lineno, "Docstring")
    return _dedupe(findings)


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
            # find numbers on lines mentioning the keyword
            for j in range(lo, hi):
                if (j + 1) in dead:
                    continue
                code_line = lines[j]
                stripped = code_line.strip()
                if stripped.startswith(("#", "//", "*", "/*")):
                    continue
                if key_simple not in code_line.lower():
                    continue
                # skip the comment's own line(s)
                if c.line <= j + 1 <= c.end_line:
                    continue
                for nm in _NUMBER_IN_CODE.finditer(_strip_strings(code_line)):
                    try:
                        actual = float(nm.group(1))
                    except ValueError:
                        continue
                    # ignore line numbers / indices / years / versions
                    if actual in (claimed,):
                        break
                    if actual > 1900 and actual < 2100 and claimed > 1900 and claimed < 2100:
                        break
                    if actual in (0, 1) and claimed in (0, 1):
                        break
                    # require the code number to be a "config-like" literal (assignment/comparison/call arg)
                    if not re.search(r"[=:(\[,<>]", code_line):
                        continue
                    findings.append(Finding(
                        path=facts.path, line=c.line, end_line=c.end_line,
                        checker="number-drift", severity="drift",
                        title=f"Comment says {key_simple} {m.group(2)}{unit or ''} but code uses {nm.group(1)}",
                        claim=f"{key_simple} = {m.group(2)}{unit or ''}",
                        evidence=f"{facts.path}:{j + 1}: {code_line.strip()[:120]}",
                        fix="Update the comment to the real value, or better: define a named constant and reference it from both.",
                        confidence=0.6,
                    ))
                    break
                else:
                    continue
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


CHECKERS = {
    "stale-symbol-ref": check_stale_symbol,
    "stale-file-ref": check_stale_file,
    "stale-import": check_stale_import,
    "number-drift": check_number_drift,
    "fragile-anchor": check_fragile_anchor,
}

CHECKER_DESCRIPTIONS = {
    "stale-symbol-ref": "Comment names a call (foo() or `foo()`) that is not defined, imported, or used in-file (v2: import- and scope-aware).",
    "stale-file-ref": "Comment claims a path inside this repo's tree that does not exist (namespace- and placeholder-aware).",
    "stale-import": "Resolvable `from M import N` where the module is missing or N is not defined, re-exported, or a submodule there.",
    "number-drift": "Comment states a magic number (timeout/port/limit) that disagrees with adjacent code.",
    "fragile-anchor": "Line-number anchors, see-above/below, or untracked HACK/WORKAROUND markers.",
}

# Intentionally unimplemented: docstring contracts and commented-out code
# are covered more precisely by darglint/pydoclint, eslint-plugin-jsdoc,
# and Ruff ERA001. `explain <id>` points users at them.
REMOVED_CHECKERS = {
    "param-mismatch": "removed in v2 (use darglint/pydoclint for Python, eslint-plugin-jsdoc check-param-names/require-param for JS/TS).",
    "raises-mismatch": "removed in v2 (use darglint DAR402/pydoclint DOC502-503 for Python, eslint-plugin-jsdoc require-throws for JS/TS).",
    "return-mismatch": "removed in v2 (use darglint DAR201-202/pydoclint DOC201-203 for Python, eslint-plugin-jsdoc require-returns-check for JS/TS).",
    "commented-code": "removed in v2 (use Ruff ERA001 for Python, eslint-plugin-comment-cleaner for JS/TS).",
}


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple] = set()
    out: list[Finding] = []
    for f in findings:
        key = (f.path, f.line, f.checker, f.title)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out
