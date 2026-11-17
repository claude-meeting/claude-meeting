"""Write the Mac app's texts that come from elsewhere, so there is one text to keep up:

- MeetingCopilotCore/StringsHans.swift: the app's Simplified Chinese texts, from its Traditional ones in
  Strings.swift, with the engine's conversion (i18n.to_hans).
- MeetingCopilotCore/NotesMarks.swift: the words of the engine's notes the viewer looks for (the "started" line,
  the labels of items that concern the user), in every language, from the engine's own table (i18n.STRINGS).

  hans_strings.py           write the files
  hans_strings.py --check   exit 1 when a file is not what its sources give now (the tests run this)
"""
import re
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "lib"))
import i18n  # noqa: E402

CORE = APP / "macos" / "Sources" / "MeetingCopilotCore"
SOURCE = CORE / "Strings.swift"
LITERAL = r'"((?:[^"\\\n]|\\.)*)"'
ENTRY = re.compile(r'^\s*"([A-Za-z0-9_]+)":\s*\(\s*' + LITERAL + r'\s*,\s*' + LITERAL + r'\s*\),', re.M | re.S)
ESCAPES = {"0": "\0", "\\": "\\", "t": "\t", "n": "\n", "r": "\r", '"': '"', "'": "'"}
FAMILIES = ("zh", i18n.HANS, "en")
MINE = ("item_mention", "item_my_todo", "item_question_to_me")     # highlighted in the viewer


def swift_unescape(body, key=""):
    """The value of a Swift string literal's body (between the quotes). Interpolation has no fixed value."""
    out, i = [], 0
    while i < len(body):
        c = body[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        nxt = body[i + 1]
        if nxt == "(":
            raise SystemExit(f"Strings.swift: {key}: string interpolation cannot be converted; use a format")
        if nxt == "u":
            end = body.index("}", i)
            out.append(chr(int(body[i + 3:end], 16)))
            i = end + 1
            continue
        out.append(ESCAPES[nxt])
        i += 2
    return "".join(out)


def swift_literal(s):
    out = []
    for c in s:
        if c in ('"', "\\"):
            out.append("\\" + c)
        elif c == "\n":
            out.append("\\n")
        elif c == "\t":
            out.append("\\t")
        elif ord(c) < 0x20 or ord(c) == 0x7F:
            out.append(f"\\u{{{ord(c):X}}}")
        else:
            out.append(c)
    return '"' + "".join(out) + '"'


def entries():
    """(key, Traditional text) per entry of the app's table, in its order."""
    return [(m.group(1), swift_unescape(m.group(2), m.group(1)))
            for m in ENTRY.finditer(SOURCE.read_text(encoding="utf-8"))]


def render_hans():
    lines = ["// Written by app/tools/hans_strings.py from Strings.swift: change the Traditional text there and run it.",
             "// The same conversion as the engine's (i18n.to_hans): OpenCC's Taiwan-to-mainland, then its fixes.",
             "extension L {",
             "    static let hans: [String: String] = ["]
    lines += [f"        {swift_literal(key)}: {swift_literal(i18n.to_hans(zh))}," for key, zh in entries()]
    lines += ["    ]", "}", ""]
    return "\n".join(lines)


def render_marks():
    started = []
    for fam in FAMILIES:
        m = re.search(r"\n\n(.+?)\{start\}", i18n.STRINGS[fam]["notes_title"])
        started.append(m.group(1))
    mine = [i18n.STRINGS[fam][key] for fam in FAMILIES for key in MINE]

    def array(values):
        return "[" + ", ".join(swift_literal(v) for v in dict.fromkeys(values)) + "]"
    return "\n".join([
        "// Written by app/tools/hans_strings.py from the engine's texts (app/lib/i18n.py STRINGS): run it after",
        "// changing them. The words of the live notes the viewer looks for, in every language the engine writes.",
        "public enum NotesMarks {",
        "    /// How the line under a meeting's title starts (notes_title).",
        f"    public static let started = {array(started)}",
        "    /// Labels of the items that concern the user (mentioned, my to-do, asked me): highlighted.",
        f"    public static let mine = {array(mine)}",
        "}",
        ""])


FILES = {CORE / "StringsHans.swift": render_hans, CORE / "NotesMarks.swift": render_marks}


def main():
    stale = []
    for path, render in FILES.items():
        text = render()
        if "--check" in sys.argv[1:]:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(path.name)
            continue
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}")
    if stale:
        sys.exit(f"out of date: {', '.join(stale)}: run app/tools/hans_strings.py")
    if "--check" in sys.argv[1:]:
        print(f"up to date ({len(entries())} app texts)")


if __name__ == "__main__":
    main()
