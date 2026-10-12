"""entry point for every hook: json in on stdin, json out on stdout, exit 0.

a crash anywhere fails open: the call goes on as if the plugin were not here,
and the user is told.
"""

import json
import sys

from . import after, before, end, jev, start

HANDLERS = {
    "PostToolUse": after.handle,
    "PreToolUse": before.handle,
    "UserPromptSubmit": start.handle,
    "Stop": end.handle,
}


def run(raw):
    try:
        event = json.loads(raw or "{}")
    except ValueError:
        return None
    name = event.get("hook_event_name")
    handler = HANDLERS.get(name)
    if handler is None:
        return None
    try:
        return handler(event)
    except Exception as exc:
        jev.log("crash", hook=name, error=type(exc).__name__)
        return {"systemMessage": "Jev plugin error in %s (%s); the call went on unfiltered." % (name, type(exc).__name__)}


def main():
    out = run(sys.stdin.read())
    if out:
        sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
