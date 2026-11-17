"""Small edits to a TOML file's text that keep every other line and comment.

Used by the settings window (tools/config_cli.py), the installer and the Minutes sync
(tools/setup_minutes.py). Handles `key = value` entries inside `[section]` tables (dotted names
included), multi-line arrays, strings that contain `#` or brackets, headers written with spaces
or a trailing comment, and keeps the file's own line ending.
"""
import json
import re
import tomllib


def toml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            raise ValueError(f"not a TOML number: {value}")
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_key(k)} = {toml_value(v)}" for k, v in value.items()) + " }"
    if value is None:
        raise ValueError("TOML has no null: delete the key instead")
    return json.dumps(str(value), ensure_ascii=False)


def _key(key):
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key, ensure_ascii=False)


def _scan(line):
    """(code, comment, bracket depth change) with quotes respected: '#', '[' and ']' inside a string
    are text, not syntax."""
    quote, depth, i = None, 0, 0
    while i < len(line):
        c = line[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "#":
            return line[:i], line[i:], depth
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
        i += 1
    return line, "", depth


def _code(line):
    return _scan(line)[0]


def _header(line):
    """'a.b' for a `[ a . b ]` header line (comment allowed), else None."""
    code = _code(line).strip()
    if code.startswith("[") and code.endswith("]") and not code.startswith("[["):
        return ".".join(p.strip() for p in code[1:-1].split("."))
    return None


def _section(lines, section):
    """(start, end) line indexes of [section]'s body, or (None, None)."""
    want = ".".join(p.strip() for p in section.split("."))
    start = next((i for i, l in enumerate(lines) if _header(l) == want), None)
    if start is None:
        return None, None
    end = next((i for i in range(start + 1, len(lines)) if _code(lines[i]).lstrip().startswith("[")), len(lines))
    return start, end


def _entry(lines, start, end, key):
    """(first, after) line indexes of `key = ...` in the section, spanning a multi-line value."""
    pattern = re.compile(rf"^\s*(?:{re.escape(key)}|{re.escape(json.dumps(key))})\s*=")
    for i in range(start + 1, end):
        if pattern.match(_code(lines[i])):
            depth, j = 0, i
            while j < end:
                depth += _scan(lines[j])[2]
                j += 1
                if depth <= 0:
                    break
            return i, j
    return None, None


def _newline(text):
    return "\r\n" if "\r\n" in text else "\n"


def set_key(text, section, key, value):
    """Set key = value inside [section], adding the key or the section when missing. An empty section
    name means the top of the file (keys before the first header)."""
    nl = _newline(text)
    line = f"{_key(key)} = {toml_value(value)}"
    lines = text.splitlines()
    if section:
        start, end = _section(lines, section)
    else:
        start, end = -1, next((i for i, l in enumerate(lines) if _code(l).lstrip().startswith("[")), len(lines))
    if start is None:
        return text.rstrip("\r\n") + (nl * 2 if text.strip() else "") + f"[{section}]{nl}{line}{nl}"
    first, after = _entry(lines, start, end, key)
    if first is not None:
        code, comment, _ = _scan(lines[first]) if after == first + 1 else ("", "", 0)
        pad = code[len(code.rstrip()):] if comment else ""
        lines[first:after] = [line + pad + comment]
    else:
        insert = end
        while insert > start + 1 and not lines[insert - 1].strip():
            insert -= 1
        lines.insert(insert, line)
    return nl.join(lines) + nl


def del_key(text, section, key):
    """Remove key from [section] if present; the section header stays."""
    nl = _newline(text)
    lines = text.splitlines()
    start, end = _section(lines, section)
    if start is None:
        return text
    first, after = _entry(lines, start, end, key)
    if first is None:
        return text
    del lines[first:after]
    return nl.join(lines) + nl


def del_empty_section(text, section):
    """Remove [section]'s header once nothing but blank lines or comments remain under it."""
    nl = _newline(text)
    lines = text.splitlines()
    start, end = _section(lines, section)
    if start is None or any(_code(l).strip() for l in lines[start + 1:end]):
        return text
    del lines[start:end]
    while lines and not lines[-1].strip():
        lines.pop()
    return nl.join(lines) + nl if lines else ""


def get_key(text, section, key):
    """The parsed value of key in [section] ('a.b' allowed), or None (the whole text is parsed)."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    for part in section.split(".") if section else []:
        data = data.get(part.strip()) if isinstance(data, dict) else None
    return data.get(key) if isinstance(data, dict) else None
