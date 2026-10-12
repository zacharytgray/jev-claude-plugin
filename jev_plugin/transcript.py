"""what the hooks need from the session: the user's request, the agent's last
words before a call, and the tool results of this turn. code only.

the transcript is written asynchronously and may lag, so every reader here
falls back quietly. the request is also saved by the UserPromptSubmit hook,
which is the first place to look.
"""

import json
import os

from . import config

TAIL_BYTES = 4 << 20


def _state_path(session_id):
    safe = "".join(c for c in str(session_id or "none") if c.isalnum() or c in "-_")
    return os.path.join(config.home(), "state", safe + ".json")


def save_request(session_id, prompt):
    os.makedirs(os.path.join(config.home(), "state"), exist_ok=True)
    with open(_state_path(session_id), "w") as f:
        json.dump({"request": prompt}, f)


def saved_request(session_id):
    try:
        with open(_state_path(session_id)) as f:
            return json.load(f).get("request") or ""
    except (OSError, ValueError, AttributeError):
        return ""


def entries(path):
    """transcript lines, oldest first, from the last few MB only."""
    if not path:
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            raw = f.read()
    except OSError:
        return []
    out = []
    for line in raw.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _content(item):
    msg = item.get("message") or {}
    return msg.get("content")


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text") or "")
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


def _is_prompt(item):
    if item.get("type") != "user" or item.get("isMeta"):
        return False
    content = _content(item)
    if isinstance(content, str):
        return bool(content.strip()) and not content.startswith("<")
    if isinstance(content, list):
        kinds = {b.get("type") for b in content if isinstance(b, dict)}
        return "text" in kinds and "tool_result" not in kinds
    return False


def last_request(items):
    for item in reversed(items):
        if _is_prompt(item):
            return _text_of(_content(item)).strip()
    return ""


def last_agent_text(items, limit=600):
    """the agent's most recent words, the why for a Read."""
    for item in reversed(items):
        if _is_prompt(item):
            return ""
        if item.get("type") == "assistant":
            text = _text_of(_content(item)).strip()
            if text:
                return text[-limit:]
    return ""


def turn_results(items):
    """[(tool_name, input, result_text)] since the last user prompt, oldest first."""
    calls = {}
    out = []
    start = 0
    for i, item in enumerate(items):
        if _is_prompt(item):
            start = i
    for item in items[start:]:
        content = _content(item)
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                calls[block.get("id")] = (block.get("name") or "", block.get("input") or {})
            elif block.get("type") == "tool_result":
                name, inp = calls.get(block.get("tool_use_id"), ("", {}))
                out.append((name, inp, _text_of(block.get("content"))))
    return out
