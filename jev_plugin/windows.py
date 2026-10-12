"""overlapping windows: cut, merge, render. code only, no jev here.

a window holds at most MAX_LINES lines and MAX_CHARS characters. the next
window starts half way into the one before, so every line but the first and
last stride sits in two windows, and a span up to half a window long always
sits whole in one of them.
"""

import re

MAX_LINES = 40
MAX_CHARS = 4000

# one long line still has to fit in a request
LINE_CAP = MAX_CHARS


def cut(lines, max_lines=MAX_LINES, max_chars=MAX_CHARS):
    """-> list of (start, end) line index ranges, end exclusive."""
    n = len(lines)
    if n == 0:
        return []
    out = []
    start = 0
    while True:
        end, size = start, 0
        while end < n and end - start < max_lines:
            add = min(len(lines[end]), LINE_CAP) + 1
            if end > start and size + add > max_chars:
                break
            size += add
            end += 1
        out.append((start, end))
        if end >= n:
            return out
        step = max(1, (end - start + 1) // 2)
        start += step


def window_text(lines, span):
    a, b = span
    return "\n".join(line[:LINE_CAP] for line in lines[a:b])


# spans code must never split: a python traceback, and a java or js stack
_TB_START = re.compile(r"^Traceback \(most recent call last\):")
_TB_END = re.compile(r"^\S.*?(Error|Exception|Exit|Interrupt)\b")
_AT = re.compile(r"^\s+at\s")


def atomic_spans(lines):
    spans = []
    i, n = 0, len(lines)
    while i < n:
        if _TB_START.match(lines[i]):
            j = i + 1
            while j < n and not _TB_END.match(lines[j]):
                j += 1
            spans.append((i, min(j + 1, n)))
            i = j + 1
            continue
        if _AT.match(lines[i]):
            j = i
            while j < n and _AT.match(lines[j]):
                j += 1
            spans.append((max(0, i - 1), j))
            i = j
            continue
        i += 1
    return spans


def merge(n, spans, kept, flagged, atomic=(), keep_tail=0):
    """decide each line from its windows.

    kept[i] and flagged[i] are the verdicts for spans[i]. flagged[i] is a bool,
    or the set of line indexes the injection filter narrowed that window down to.
    a line is shown when any window holding it is kept; it is withheld when every
    window holding it flags it. returns (show, withheld), two lists of bools of
    length n.
    """
    show = [False] * n
    seen = [0] * n
    hot = [0] * n
    for (a, b), k, f in zip(spans, kept, flagged):
        for i in range(a, b):
            seen[i] += 1
            if k:
                show[i] = True
            if f is True or (isinstance(f, (set, frozenset)) and i in f):
                hot[i] += 1
    withheld = [seen[i] > 0 and hot[i] == seen[i] for i in range(n)]
    for a, b in atomic:
        if any(show[a:b]):
            for i in range(a, b):
                show[i] = True
    for i in range(max(0, n - keep_tail), n):
        show[i] = True
    return show, withheld


def ranges(flags):
    """[(start, end)] runs of True, end exclusive."""
    out, start = [], None
    for i, f in enumerate(flags):
        if f and start is None:
            start = i
        elif not f and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(flags)))
    return out


def label(a, b, first=1):
    """1-based inclusive label for a 0-based half-open range."""
    a, b = a + first, b - 1 + first
    return str(a) if a == b else "%d-%d" % (a, b)


def render_numbered(lines, show, withheld, first=1):
    """for Read: the renderer numbers lines from startLine, so every line keeps
    its place. returns (start_line, text) or None when nothing is shown.

    lines before the first shown line go through startLine; lines after the
    last are dropped; a hidden run in between becomes one marker line and
    blank lines, so the numbers after it stay right.
    """
    visible = [s and not w for s, w in zip(show, withheld)]
    marked = [s and w for s, w in zip(show, withheld)]
    keep = [v or m for v, m in zip(visible, marked)]
    if not any(keep):
        return None
    lo = keep.index(True)
    hi = len(keep) - keep[::-1].index(True)
    out = []
    i = lo
    while i < hi:
        if visible[i]:
            out.append(lines[i])
            i += 1
            continue
        j = i
        withheld_run = marked[i]
        while j < hi and not visible[j] and marked[j] == withheld_run:
            j += 1
        if withheld_run:
            note = "[lines %s withheld by the Jev injection filter]" % label(i, j, first)
        else:
            note = "[lines %s hidden by the Jev filter]" % label(i, j, first)
        out.append(note)
        out.extend([""] * (j - i - 1))
        i = j
    return first + lo, "\n".join(out)


def render_plain(lines, show, withheld):
    """for Bash: no renderer numbers, so a hidden run collapses to one line."""
    out = []
    i, n = 0, len(lines)
    while i < n:
        if show[i] and not withheld[i]:
            out.append(lines[i])
            i += 1
            continue
        j = i
        w = show[i] and withheld[i]
        while j < n and not (show[j] and not withheld[j]) and (show[j] and withheld[j]) == w:
            j += 1
        if w:
            out.append("[output lines %s withheld by the Jev injection filter]" % label(i, j))
        else:
            out.append("[output lines %s hidden by the Jev filter]" % label(i, j))
        i = j
    return "\n".join(out)
