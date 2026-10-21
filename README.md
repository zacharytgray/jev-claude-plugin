# jev-claude-plugin

A Claude Code plugin that puts Jev, TypeSafe's System One model, at four points around the agent's tool calls. The agent
loop stays as it is. Code settles what it can first; Jev answers small typed questions only where code cannot. Every
feature has a switch, and every hook fails open.

The design, its open questions and the evidence behind each choice live in the `jev-harness` repo under `product/`
(`ARCHITECTURES.md`, "The converged design"). Nothing here is admitted as the product design yet.

## The four points

| point | hook | what it does | starts |
|---|---|---|---|
| after the call | `PostToolUse` on every tool | cuts long output into windows of at most 40 lines and 4,000 characters, each overlapping the next by half; each window, with its path and line range, gets two Jev requests, one for relevance and one for injection; keeps windows with relevance of 0.1 or more; windows flagged at 0.5 or more that overlap or touch merge into one region, where a Jev line finder picks the first and last planted line, as in the jev-harness paper, and only that span is withheld, then the region is asked again until it is clear; Read keeps its original line numbers. Every other tool except those that bring in no outside text (Edit, Write, TodoWrite, Agent and a few more) gets the injection check only, on each text field of its response; a background subagent's reply is not seen by the hook, but its own tool calls are | on |
| before the call | `PreToolUse`, every tool | Jev auto mode, in place of Claude Code's own, autonomy first: a call is allowed unless one of eight hazards is hot (publishing, messaging a person, unrecoverable deletes, a secret shown or sent, a live guard weakened, a live service disrupted, private messages read, money spent), and your request can open a hot hazard; allow, deny with a reason, or ask you. Code only denies a force push, asks on your never-allow list, sudo, network-to-shell, credential paths and printing a secret file, and allows plain reads | off |
| start | `UserPromptSubmit` | suggests an effort and a model for the request (shown to you; no hook can set either), and hints skills from your words | off |
| end | `Stop` | splits the final reply into claims and checks each against this turn's tool results; sends the agent back once if a claim has nothing behind it | off |

Outputs of 4,000 characters or less skip relevance and are only checked for injection. Bash stderr is checked for
injection too, and a line longer than 4,000 characters is checked in pieces, so every character the agent sees has
been screened. The saved full Bash output keeps what relevance hid, never what injection withheld. If the line finder finds nothing,
fails, or needs more than 10 Jev calls for a region, the whole region stays withheld. On a failure the hook can see
(a traceback, a failed test summary), the question becomes "why did this fail", stack traces are never split, and the
last 20 lines always stay. When no window passes, the plain output goes through.

Jev auto mode answers every tool call that can touch the machine, in every permission mode but `bypassPermissions`,
so it stands in for Claude Code's own auto mode. One Jev request
per call holds ten yes/no questions: the eight hazards, whether your request asked for the operation, and whether it
ruled the operation out. Jev sees your latest request, the agent's message that request answered (so "yes, do that"
reads against the proposal) and the agent's own stated reason, never text from files or tool output as your words. A
hazard your request asked for is allowed at 0.60 (private data at 0.70). A request never opens a secret, and a secret
leaving the machine is denied. A call your request ruled out ("do not push", "no paid API calls") is held.

Run Claude Code in auto mode with the plugin on. Then exactly one judge decides each call:

- Jev allows or denies: that answer stands, and Claude Code's auto mode classifier does not run on the call.
- Jev holds a call: it goes to the classifier alone. A hook's ask in auto mode would still let the classifier deny,
  so Jev steps aside instead of stacking a second judge. Your never-allow list still always comes to you.
- Jev is down, times out, or there is no key: the plugin steps aside, floor included, and the classifier decides.

In the default mode, a held call comes to you as a normal prompt, and a failure leaves Claude Code's usual prompts. In
headless `claude -p` without auto mode, held calls are refused. Checked on Claude Code 2.1.305 with headless auto mode
runs: a call Jev allowed skipped the classifier (decided in under 40 ms, and an exfiltration-shaped command the
classifier blocks on its own ran), and with Jev unreachable the classifier blocked that same command. In a sandbox
check on 2.1.296, `PreToolUse` fired for read-only Bash commands and for Reads inside the project, which
`PermissionRequest` never saw.

The questions come from an earlier autonomy-first judge, chosen on 399 tool calls from one user's transcripts
(`docs/autonomy-first-auto-mode.md`). On the plugin's general wording, its lines were too high. On 301 hand-labelled
calls (jev-1.14.0, 2026-10-20), the lines at 0.6 of the earlier ones and a request question that says a wider target
is not asked for caught 138 of 159 stops and wrongly stopped 2 of 142 allowed calls. Every secret leaving the
machine (37), unasked destruction (19), fetched code (8) and outward upload (8) was stopped.

What the paid tests changed (round 25, 8,801 Jev requests in all; the fixes were rerun on the same items):
- Each window goes to Jev as `{path, lines, text}`, as in the jev-harness paper. With the bare text, injection caught
  61 to 64 of 100 planted attacks; with path and lines, 98, and flagged 0 of 50 clean chunks. Relevance with the same
  shape kept 0.973 of needed lines at the 0.1 cut. Relevance and injection are asked in separate requests.
- Jev auto mode starts off. Behind the generic floor, the round 25 judge still allowed 68 of 344 calls labelled ask or
  deny, all under rules about one user's machine. List your own never-allow paths and commands in `config.json` as
  `"never_allow": ["~/.private-dir", "some-command"]`; matching calls always come to you. A path the call names
  counts when it resolves under a listed folder, so `../../.private-dir` and quoted paths match too. Read-only MCP tools
  (search, get, list and similar) are described to Jev as lookups.
- The claims check stays off. Its evidence is the lines that share the claims' words, and its cut is 0.3. That sends
  back 9.3% of passing runs but only 28.0% of failing ones, because many wrong answers quote real text.

## Use

Install it from this repository's marketplace, inside Claude Code:

```
/plugin install jev --marketplace <owner>/jev-claude-plugin
```

Or try it for one session from a checkout:

```sh
export TYPESAFE_API_KEY=...          # never commit it
claude --plugin-dir path/to/jev-claude-plugin
```

Claude Code must see `TYPESAFE_API_KEY` in its environment. Without a working key, each feature steps aside and says
so once per session (auto mode) or per call (the others).

Ask the agent to turn a feature on or off ("turn off the Jev filter"); the `jev-switches` skill changes the config file
at `~/.claude/jev-plugin/config.json`. Or run `sh bin/jev-switch` directly. Decisions and scores go to
`~/.claude/jev-plugin/log.jsonl`; whole outputs are never logged. When the filter hides part of a Bash output, the full
output is saved under `~/.claude/jev-plugin/outputs/` and the agent is told the path.

## Failure handling

No key, a Jev error, a timeout (4 seconds per request, 20 per hook), more than 128 windows, or a crash: the call goes
on as if the plugin were not here, and both the agent and you see a one-line notice.

## Verify

```sh
python3 -m unittest discover -s tests -t .
```

`tests/fake_jev.py` is a fake Jev endpoint for checking the wiring in a real Claude Code session without a key
(`JEV_API_URL` points the client at it).
