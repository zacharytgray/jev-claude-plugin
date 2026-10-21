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


EARLIER = 2


def save_request(session_id, prompt):
    """the newest prompt, and the two before it: "yes, push it" needs its context."""
    os.makedirs(os.path.join(config.home(), "state"), exist_ok=True)
    old = _load(session_id)
    earlier = [x for x in (old.get("earlier") or []) + [old.get("request")] if x][-EARLIER:]
    # write then rename, so a hook never reads half a request
    path = _state_path(session_id)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"request": prompt, "earlier": earlier}, f)
    os.replace(tmp, path)


def _load(session_id):
    try:
        with open(_state_path(session_id)) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def saved_earlier(session_id):
    return [x for x in _load(session_id).get("earlier") or [] if isinstance(x, str)]


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


def last_requests(items, n=EARLIER + 1):
    """the user's last n prompts, oldest first."""
    out = []
    for item in reversed(items):
        if _is_prompt(item):
            out.append(_text_of(_content(item)).strip())
            if len(out) >= n:
                break
    return out[::-1]


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


def previous_agent_text(items, request="", limit=600):
    """what the latest request answered: the agent's words between the user's
    last two prompts, so "yes, do that" reads against the proposal. when the
    transcript lags behind the saved request, the newest prompt is not in it
    yet, and the answer is the tail."""
    prompts = [i for i, item in enumerate(items) if _is_prompt(item)]
    if not prompts:
        span = items if request else []
    elif request and _text_of(_content(items[prompts[-1]])).strip() != request.strip():
        span = items[prompts[-1] + 1:]
    else:
        span = items[(prompts[-2] + 1 if len(prompts) > 1 else 0):prompts[-1]]
    texts = [_text_of(_content(item)).strip() for item in span if item.get("type") == "assistant"]
    return "\n".join(t for t in texts if t)[-limit:]


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
                text = _text_of(block.get("content"))
                # keep the error mark: a failed call is not a passing check
                out.append((name, inp, ("[tool error] " + text) if block.get("is_error") else text))
    return out
