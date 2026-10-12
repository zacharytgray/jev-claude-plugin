---
name: jev-switches
description: Turn a feature of the Jev plugin on or off, or show which are on. Use when the user asks to enable, disable, pause, or check the Jev filter, Jev auto mode, the start hints (effort, model, skills), or the claims-backed check at the end.
---

# Jev switches

The Jev plugin reads one config file on every hook call. Change it only with the script below, and only for what the
user asked. A change applies from the next tool call; no restart is needed.

Show every switch and its state:

```sh
sh "${CLAUDE_PLUGIN_ROOT}/bin/jev-switch"
```

Change one:

```sh
sh "${CLAUDE_PLUGIN_ROOT}/bin/jev-switch" <name> on|off
```

| name | what it does |
|---|---|
| `after_call.relevance` | hide the parts of long Read and Bash output that do not answer the agent's why |
| `after_call.injection` | withhold lines of Read and Bash output that try to give the agent a task of their own |
| `before_call.auto_mode` | Jev answers permission prompts: allow, deny with a reason, or ask the user when unsure |
| `start.effort` | suggest a reasoning effort per request, shown to the user |
| `start.model` | suggest a model per request, shown to the user |
| `start.skills` | hint skills that may fit the request, from the user's words only |
| `end.claims_backed` | at the stop, send the agent back once if a claim has no tool result behind it |

Map plain requests to names: "the filter" means both `after_call` switches; "auto mode" or "permission prompts" means
`before_call.auto_mode`. After a change, show the user the new state from the script's output. If the user asks to turn
off the injection filter, say once that it guards against planted instructions, then do what they asked.

To keep a path or command away from Jev auto mode for good, add it to the `never_allow` list in
`~/.claude/jev-plugin/config.json` (plain text; `~` means the home folder). Matching calls always go to the user.
