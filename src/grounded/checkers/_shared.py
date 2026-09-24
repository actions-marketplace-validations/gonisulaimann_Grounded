"""Shared vocabulary and helpers for every checker: builtin/keyword
lists, reference-verb and placeholder patterns, comment-block utilities,
the commented-out-code detector, and finding de-duplication.
"""
from __future__ import annotations

import ast
import difflib
import re
import sys

from ..models import Comment, FileFacts, Finding
from ..repo_index import RepoIndex


# Directory names the scanner never walks (see config.DEFAULT_IGNORE_DIRS
# plus the always-skipped cache dirs). A relative import pointing under
# one of these has targets the snapshot cannot see: never a missing-module
# verdict (see _js_target_in_ignored_dir).
IGNORED_DIR_NAMES = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".tox",
    "dist", "build", ".next", "out", "coverage", ".nyc_output",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "vendor", "third_party",
})

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
    # Function/constant builtins the set missed (measured: `locals()` in
    # rich's README reported as a repo lie). Deliberately NOT the
    # exception hierarchy: libraries shadow those names with their own
    # exceptions (`requests.ConnectionError`), so they stay checkable.
    "all", "any", "ascii", "bin", "breakpoint", "bytes", "bytearray",
    "chr", "compile", "complex", "delattr", "dir", "eval", "exec",
    "exit", "quit", "format", "frozenset", "globals", "hash", "help",
    "hex", "locals", "memoryview", "oct", "ord", "slice", "vars",
    "aiter", "anext", "__import__", "Ellipsis", "NotImplemented",
    "copyright", "credits", "license",
}

# Metasyntactic call names: `foo()`, `bar()`, `blah()` in a comment are
# the author's "some function", never a reference (seen: svelte's
# "`foo` in `foo.bar` or `foo()`" reported as a lie). Same silence class
# as the Xxx convention and the doc/file placeholder lists. Kept to the
# Jargon-File core: `spam`/`eggs`/`thing` are real names too often.
_METASYNTACTIC_CALLS = frozenset({"foo", "bar", "baz", "blah", "qux", "quux"})

JS_GLOBALS = {
    "console", "log", "warn", "error", "info", "debug", "assert",
    "fetch", "setTimeout", "setInterval", "clearTimeout", "clearInterval",
    "Promise", "Array", "Object", "String", "Number", "Boolean", "JSON",
    "Math", "Date", "RegExp", "Error", "TypeError", "RangeError",
    "require", "module", "exports", "process", "setImmediate",
    "describe", "it", "test", "expect", "beforeEach", "afterEach",
    "useState", "useEffect", "useRef", "useMemo", "useCallback",
    # ECMAScript and platform globals (measured misses: React compiler
    # comments naming `Symbol` and `Math.random`).
    "Symbol", "Reflect", "Proxy", "Map", "Set", "WeakMap", "WeakSet",
    "BigInt", "Intl", "globalThis", "queueMicrotask", "structuredClone",
    "Atomics", "SharedArrayBuffer", "ArrayBuffer", "DataView",
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

# Go standard-library package leaf names: doc examples calling
# fmt.Printf or time.Now reference the toolchain, not the repo.
# (Seen: gin docs.) Checked on the call root only.
_GO_STDLIB_PACKAGES = frozenset({
    "fmt", "log", "time", "os", "io", "bytes", "strings", "errors",
    "context", "sync", "sort", "strconv", "regexp", "path", "filepath",
    "math", "net", "http", "url", "json", "template", "sql", "rpc",
    "crypto", "tls", "encoding", "base64", "hex", "mime", "mail",
    "reflect", "runtime", "atomic", "maps", "slices", "cmp", "hash",
    "bufio", "exec", "signal", "unicode", "utf8", "html", "image",
    "compress", "archive", "container", "heap", "list", "ring",
    "testing", "httptest", "plugin", "debug", "expvar", "flag",
    "syscall", "unsafe",
})

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
    # Measured misses (jq, curl, redis comments): stdio char I/O, Linux and
    # BSD/Solaris event APIs, and the kernel-style container_of macro.
    "fgetc", "fputc", "getc", "putc", "ungetc", "fgets", "fputs", "fflush",
    "eventfd", "epoll_wait", "epoll_ctl", "epoll_create", "kqueue", "kevent",
    "port_get", "port_getn", "port_associate", "port_create", "container_of",
    "vfork", "clone", "setjmp", "longjmp", "atexit", "getenv", "setenv",
    # More syscalls/libc named in Go and Python comments (kubernetes,
    # cpython): measured misses.
    "openat", "umount", "umount2", "mount", "getpagesize", "wcsdup",
    "flockfile", "funlockfile", "fcntl", "ioctl", "sysconf", "mprotect",
    "posix_spawn", "execv", "pread", "pwrite", "renameat", "unlinkat",
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
    if language == "c" and base.endswith("s") and base[:-1] in C_STDLIB_FUNCS:
        return True  # pluralized call in prose: "the parent forks()"
    if language in ("go", "python") and base in C_STDLIB_FUNCS and base not in (
            "open", "read", "write", "close", "time", "stat", "select", "link", "send"):
        # Syscall/libc names in Go and Python comments (`openat()`,
        # `getpagesize()`); generic verbs stay checkable in those languages.
        return True
    return False


# Win32-style API names (`DsMakeSPN`, `GetLastError`): CamelCase with no
# underscore. C codebases namespace their own functions (`Curl_`, `RM_`,
# `sqlite3_`), so in C comments these are platform calls, not repo claims.
_C_PLATFORM_CAMEL = re.compile(r"[A-Z][a-z]+(?:[A-Z][A-Za-z0-9]*)+")


_WINDOWS_MODULES = frozenset({"_winapi", "_overlapped", "msvcrt", "winreg", "_winreg",
                              "win32api", "win32file", "win32con", "pywintypes", "wmi"})


def _windows_context(facts: FileFacts) -> bool:
    cached = facts.__dict__.get("_windows_context")
    if cached is None:
        cached = bool(_WINDOWS_MODULES & set(facts.imports.values())) or any(
            re.search(r"\b(Windows|Win32|WinAPI|IOCP)\b", c.text) for c in facts.comments)
        facts.__dict__["_windows_context"] = cached
    return cached


def _external_or_family(base: str, facts: FileFacts, index: RepoIndex) -> bool:
    """Names a comment may cite that the snapshot cannot own.

    * `_suffix()` fragments name a family by its shared tail (curl's
      "its `_active()` method" -> `cf_socket_active`), when some repo
      symbol ends with the fragment.
    * C only: Win32-style CamelCase APIs, and functions whose prefix is a
      system-included library header (`ares_process()` with `<ares.h>`).
    """
    if base.startswith("_") and len(base) >= 4 and base in index.underscore_suffixes():
        return True
    if facts.language == "javascript" and base[:1].isupper() and "_" not in base \
            and base not in index.all_symbols and _C_PLATFORM_CAMEL.fullmatch(base):
        # PascalCase calls in JS comments are ECMAScript abstract operations
        # (`IsAnonymousFunctionDefinition()`) or constructors: repo functions
        # are camelCase, and a repo class would be in the index.
        return True
    if facts.language == "python" and "_" not in base and base not in index.all_symbols \
            and _C_PLATFORM_CAMEL.fullmatch(base) and _windows_context(facts):
        # Win32 APIs in Python comments (`CreateFile()`, cpython asyncio).
        # Only with Windows evidence: elsewhere a PascalCase call is a
        # class, and a renamed class must keep firing.
        return True
    if facts.language == "c":
        if "_" not in base and _C_PLATFORM_CAMEL.fullmatch(base):
            return True
        head = base.split("_", 1)[0]
        if "_" in base and facts.imports.get(head) not in (None, ""):
            return True
    return False

PLACEHOLDER_PATH_HINTS = {"example", "examples", "path", "to", "foo", "bar", "baz",
    "placeholder", "sample", "demo", "<", ">", "...", "xxx",
    "myapp", "mysite", "app_label", "yourproject", "yourdomain", "sitename"}

# Metasyntactic path parts. `my`-prefixed nouns (`mypkg`, `my_app`) are the
# docstring convention for "your package", and x/y/z are the classic
# variable stems: `src/mypkg/x.py` in a comment explains a layout, it does
# not claim a file (seen: Grounded's own `src/mypkg/x.py` and `src/old/x.py`
# layout notes, reported as lies once subdirectory scans indexed the whole
# project). A real file with that name still resolves first.
_PLACEHOLDER_PATH_SEGMENT = re.compile(
    r"my[_-]?(?:pkg|package|module|mod|lib|library|app|application|project|"
    r"proj|file|dir|folder|repo|service|component|plugin|script|code)s?")
_PLACEHOLDER_PATH_STEMS = frozenset({"x", "y", "z"})


def _is_placeholder_path(ref: str) -> bool:
    parts = [s for s in ref.lower().split("/") if s]
    if not parts:
        return False
    stem = parts[-1].rsplit(".", 1)[0]
    if stem in _PLACEHOLDER_PATH_STEMS:
        return True
    if any("xxx" in p for p in parts):
        return True  # `utils/dummy_xxx_objects.py`: Xxx convention inside a name
    return any(_PLACEHOLDER_PATH_SEGMENT.fullmatch(s) for s in parts[:-1] + [stem])


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
    r"necessary|available)|never|no longer|no\s+calls?\b|instead|avoid|avoids|"
    r"unsupported)\b", re.IGNORECASE)

# v2: illustrative-example markers. Docs constantly invent paths
# ("For example ... ``django/templatetags/news/photos.py``"); a file ref
# within ~120 chars after such a marker is an example, not a claim.
# Dotted abbreviations need lookarounds, not \b: after "e.g." comes a
# space (non-word), so a trailing \b never matches in real prose.
_ILLUSTRATIVE = re.compile(
    r"\b(for example|for instance|such as|suppose|imagine|example)\b"
    r"|(?<!\w)e\.g\.(?!\w)|(?<!\w)i\.e\.(?!\w)",
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


# Python data-model names a dunder typo is most likely aiming at, besides
# the dunders the repo itself defines.
_DATA_MODEL_DUNDERS = frozenset(n for n in dir(object) + dir(type) + [
    "__getitem__", "__setitem__", "__delitem__", "__len__", "__iter__",
    "__next__", "__contains__", "__enter__", "__exit__", "__aenter__",
    "__aexit__", "__aiter__", "__anext__", "__await__", "__call__",
    "__get__", "__set__", "__delete__", "__set_name__", "__bool__",
    "__class_getitem__", "__post_init__", "__fspath__", "__missing__",
    "__reversed__", "__index__", "__getattr__", "__all__", "__version__",
] if _is_dunder(n))


def _dunder_typo_of(name: str, index: RepoIndex) -> bool:
    """Whether a dunder is one edit away from a real one (typo class)."""
    dunders = index.dunder_symbols() if hasattr(index, "dunder_symbols") else frozenset(
        n for n in index.all_symbols if _is_dunder(n))
    if name in _DATA_MODEL_DUNDERS or name in dunders:
        return False
    memo = index.__dict__.setdefault("_dunder_typo_memo", {}) if hasattr(index, "__dict__") else {}
    if name not in memo:
        pool = sorted(_DATA_MODEL_DUNDERS | dunders)
        memo[name] = bool(difflib.get_close_matches(name, pool, n=1, cutoff=0.85))
    return memo[name]


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
    """Map of line number to comment text (multi-line blocks expanded).

    Cached per FileFacts: _nearby_ticket asks once per candidate claim, and
    rebuilding the map each time was quadratic in a file's comments (12 s
    of a cpython scan). Callers must not mutate the result."""
    cached = facts.__dict__.get("_comment_line_map")
    if cached is not None:
        return cached
    by_line: dict[int, str] = {}
    for c in facts.comments:
        for ln in range(c.line, c.end_line + 1):
            by_line.setdefault(ln, c.text)
    facts.__dict__["_comment_line_map"] = by_line
    return by_line


# Past-tense frames mark a history note, not a live claim ("We used to
# use `cgi.parse_header()` here" — measured: httpx). Unambiguous on
# purpose: "once"/"was" appear in live prose constantly.
_HISTORICAL = re.compile(r"\b(used to|formerly|previously)\b", re.IGNORECASE)

# Work-item markers: a block tracking unfinished work keeps full
# checking even when it names a ticket (the TODO may itself name a
# renamed API). Only discussion/history blocks go silent on tickets.
_WORKITEM = re.compile(r"\b(TODO|FIXME|HACK|XXX|BUG)\b")


def _comment_block_text(facts: FileFacts, line: int, end: int) -> str:
    """Text of the contiguous comment run enclosing [line, end].

    Consecutive comment lines form one thought (a history paragraph,
    a ticket-anchored discussion); a code line or blank line ends it.
    Block comments contribute their own text. Used to judge ticket and
    history framing wider than a single line without reaching into
    unrelated neighbors.
    """
    parts: list[str] = []
    for c in facts.comments:
        if c.is_block:
            if c.line <= line <= c.end_line or c.line <= end <= c.end_line:
                return c.text
            continue
        parts.append(c)
    by_line = {c.line: c.text for c in parts if c.line == c.end_line}
    if line not in by_line and end not in by_line:
        return ""
    lo, hi = line, end
    while lo - 1 in by_line:
        lo -= 1
    while hi + 1 in by_line:
        hi += 1
    return "\n".join(by_line[ln] for ln in range(lo, hi + 1))


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

    A block-comment *line* may still carry real code after the comment
    closes on the same line (`/** @param {T} x */ (x) => use(x)`).
    Blanking such a line hid the use and produced phantom absences
    (measured: svelte's `html.js` `reg_exp_entity`, called two lines below
    its definition behind an inline `/** @param ... */`, reported as a
    ghost export). The comment span is stripped, the code after it stays.
    """
    skip: set[int] = set()
    keep: dict[int, str] = {}
    for c in facts.comments:
        for ln in range(c.line, c.end_line + 1):
            if 1 <= ln <= len(facts.lines):
                line = facts.lines[ln - 1]
                s = line.strip()
                if facts.language == "python":
                    if s.startswith("#"):
                        skip.add(ln)
                else:
                    if s.startswith("//"):
                        skip.add(ln)
                    elif s.startswith(("/*", "*", "*/")):
                        rest = line.split("*/", 1)[1] if "*/" in line else ""
                        if rest.strip():
                            keep[ln] = rest
                        else:
                            skip.add(ln)
    out: list[str] = []
    for i, ln in enumerate(facts.lines, start=1):
        if i not in skip:
            out.append(keep.get(i, ln))
    return "\n".join(out)


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


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple] = set()
    out: list[Finding] = []
    for f in findings:
        key = (f.path, f.line, f.checker, f.title)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out
