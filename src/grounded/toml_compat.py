"""Minimal TOML reader used only when tomllib is unavailable (Python 3.10).

Supports exactly the subset grounded's own config needs: [table] and
[table.sub] headers, `#` comments, double/single-quoted strings, arrays
of strings, one-level inline tables with string or string-array values,
booleans, and integers. Anything else raises ValueError, which callers
treat the same as an unreadable file (never a silent half-config).
"""
from __future__ import annotations


def loads(text: str) -> dict:
    root: dict = {}
    current = root
    for raw in text.splitlines():
        line = _strip_comment(raw).strip()
        if not line:
            continue
        if line.startswith("["):
            if not line.endswith("]") or line.startswith("[["):
                # Array-of-tables ([[x]]) is outside the subset: reject,
                # never misread as a "[x]"-style key (it used to parse as
                # the literal key "[x]").
                raise ValueError(f"bad header: {raw!r}")
            current = root
            for part in line[1:-1].strip().split("."):
                part = part.strip().strip("\"'")
                if not part or not isinstance(current, dict):
                    raise ValueError(f"bad header: {raw!r}")
                current = current.setdefault(part, {})
            if not isinstance(current, dict):
                raise ValueError(f"bad header: {raw!r}")
            continue
        key, sep, val = line.partition("=")
        if not sep:
            raise ValueError(f"bad line: {raw!r}")
        key = key.strip().strip("\"'")
        if not key:
            raise ValueError(f"bad line: {raw!r}")
        _assign_dotted(current, key, _value(val.strip(), raw), raw)
    return root


def _assign_dotted(current: dict, key: str, value, raw: str) -> None:
    """Dotted keys (`a.b = 1`) nest like real TOML instead of landing as
    the flat literal key "a.b" (which silently orphaned config)."""
    parts = [p.strip().strip("\"'") for p in key.split(".")]
    if any(not p for p in parts):
        raise ValueError(f"bad key: {raw!r}")
    for part in parts[:-1]:
        nxt = current.setdefault(part, {})
        if not isinstance(nxt, dict):
            raise ValueError(f"bad key: {raw!r}")
        current = nxt
    parts_last = parts[-1]
    if parts_last in current:
        raise ValueError(f"duplicate key: {raw!r}")
    current[parts_last] = value


def _strip_comment(line: str) -> str:
    out: list[str] = []
    i, n = 0, len(line)
    quote: str | None = None
    while i < n:
        ch = line[i]
        if quote is not None:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(line[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("\"", "'"):
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "#":
            break
        out.append(ch)
        i += 1
    return "".join(out)


def _value(text: str, raw: str):
    if not text:
        raise ValueError(f"bad value: {raw!r}")
    if text[0] == "\"":
        return _basic_string(text, raw)
    if text[0] == "'":
        if len(text) < 2 or not text.endswith("'"):
            raise ValueError(f"bad value: {raw!r}")
        return text[1:-1]
    if text[0] == "[":
        return _array(text, raw)
    if text[0] == "{":
        return _inline_table(text, raw)
    if text in ("true", "false"):
        return text == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    raise ValueError(f"bad value: {raw!r}")


def _basic_string(text: str, raw: str) -> str:
    out: list[str] = []
    i, n = 1, len(text)
    closed = False
    simple = {"n": "\n", "t": "\t", "r": "\r", "\"": "\"", "\\": "\\",
              "b": "\b", "f": "\f"}
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt in simple:
                out.append(simple[nxt])
                i += 2
                continue
            if nxt in ("u", "U"):
                width = 4 if nxt == "u" else 8
                digits = text[i + 2:i + 2 + width]
                if len(digits) != width or any(
                        c not in "0123456789abcdefABCDEF" for c in digits):
                    raise ValueError(f"bad value: {raw!r}")
                out.append(chr(int(digits, 16)))
                i += 2 + width
                continue
            # Unknown escapes are a corrupt string, not a literal
            # passthrough: `\u0041` used to decode as the text "u0041".
            raise ValueError(f"bad value: {raw!r}")
        if ch == "\"":
            closed = True
            i += 1
            break
        out.append(ch)
        i += 1
    if not closed or text[i:].strip():
        raise ValueError(f"bad value: {raw!r}")
    return "".join(out)


def _split_top(text: str, raw: str) -> list[str]:
    parts, depth, cur = [], {"[": 0, "{": 0}, []
    quote: str | None = None
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if quote is not None:
            cur.append(ch)
            if ch == "\\" and i + 1 < n:
                cur.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("\"", "'"):
            quote = ch
            cur.append(ch)
        elif ch in depth:
            depth[ch] += 1
            cur.append(ch)
        elif ch == "]":
            depth["["] -= 1
            cur.append(ch)
        elif ch == "}":
            depth["{"] -= 1
            cur.append(ch)
        elif ch == "," and depth["["] == 0 and depth["{"] == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    if quote is not None or depth["["] != 0 or depth["{"] != 0:
        raise ValueError(f"bad value: {raw!r}")
    parts.append("".join(cur))
    return parts


def _array(text: str, raw: str) -> list:
    if not text.endswith("]"):
        raise ValueError(f"bad value: {raw!r}")
    inner = text[1:-1].strip()
    if not inner:
        return []
    # A single trailing comma is valid TOML (`["a",]`); the splitter
    # yields one empty tail part, which is skipped rather than rejected.
    # Interior empties (`["a",,]`) stay errors.
    parts = _split_top(inner, raw)
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    return [_value(p.strip(), raw) for p in parts]


def _inline_table(text: str, raw: str) -> dict:
    if not text.endswith("}"):
        raise ValueError(f"bad value: {raw!r}")
    inner = text[1:-1].strip()
    out: dict = {}
    if not inner:
        return out
    for part in _split_top(inner, raw):
        key, sep, val = part.partition("=")
        if not sep:
            raise ValueError(f"bad value: {raw!r}")
        key = key.strip().strip("\"'")
        if not key:
            raise ValueError(f"bad value: {raw!r}")
        out[key] = _value(val.strip(), raw)
    return out
