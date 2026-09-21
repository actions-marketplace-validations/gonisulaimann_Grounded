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
        if line.startswith("[") and line.endswith("]"):
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
        current[key] = _value(val.strip(), raw)
    return root


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
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            nxt = text[i + 1]
            out.append({"n": "\n", "t": "\t", "r": "\r", "\"": "\"", "\\": "\\"}.get(nxt, nxt))
            i += 2
            continue
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
    return [_value(p.strip(), raw) for p in _split_top(inner, raw)]


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
