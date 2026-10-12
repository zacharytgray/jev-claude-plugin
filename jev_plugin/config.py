"""switches, read by every hook on every call.

one json file at ~/.claude/jev-plugin/config.json (JEV_PLUGIN_HOME moves the
folder, for tests and the lab). a missing file or a missing key falls back to
DEFAULTS, so a half-written file never turns a feature on by surprise.
"""

import copy
import json
import os
import tempfile

DEFAULTS = {
    "after_call": {"relevance": True, "injection": True},
    "before_call": {"auto_mode": True},
    "start": {"effort": False, "model": False, "skills": False},
    "end": {"claims_backed": False},
}

# what the skill shows the user, one line per switch
DESCRIPTIONS = {
    "after_call.relevance": "hide the parts of long Read and Bash output that do not answer the agent's why",
    "after_call.injection": "withhold lines of Read and Bash output that try to give the agent a task of their own",
    "before_call.auto_mode": "Jev answers permission prompts: allow, deny with a reason, or ask you when unsure",
    "start.effort": "suggest a reasoning effort for each request (shown to you; no hook can set it)",
    "start.model": "suggest a model for each request (shown to you; no hook can set it)",
    "start.skills": "hint skills that may fit the request, from your words only",
    "end.claims_backed": "when the agent stops, send it back once if a claim in its reply has no tool result behind it",
}


def home():
    return os.environ.get("JEV_PLUGIN_HOME") or os.path.join(
        os.path.expanduser("~"), ".claude", "jev-plugin")


def path():
    return os.path.join(home(), "config.json")


def load():
    out = copy.deepcopy(DEFAULTS)
    try:
        with open(path()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return out
    if not isinstance(data, dict):
        return out
    for group, switches in DEFAULTS.items():
        got = data.get(group)
        if not isinstance(got, dict):
            continue
        for name in switches:
            if isinstance(got.get(name), bool):
                out[group][name] = got[name]
    return out


def on(cfg, dotted):
    group, name = dotted.split(".", 1)
    return bool(cfg.get(group, {}).get(name))


def set_switch(dotted, value):
    if dotted not in DESCRIPTIONS:
        raise KeyError(dotted)
    cfg = load()
    group, name = dotted.split(".", 1)
    cfg[group][name] = bool(value)
    os.makedirs(home(), exist_ok=True)
    # write then rename, so a hook never reads half a file
    fd, tmp = tempfile.mkstemp(dir=home(), prefix=".config-")
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.replace(tmp, path())
    return cfg
