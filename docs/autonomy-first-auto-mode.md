# An autonomy-first Jev auto mode (findings from ZachGPT, 2026-10-15)

ZachGPT's Pi harness had the same problem this plugin's auto mode has: it asked far too often. A run on the hub
compared four judges on real tool calls, and a new design that stops only hazards cut the asks by about ten times.
That design now runs in ZachGPT's guard (`hermes_dispatch/jevauto.py` in hermes-dispatch, `docs/jev-auto.md` there).
This note is what carries over to the plugin's `before.py`. Nothing here is admitted as the product design.

## What the user actually refuses

30 days of Claude Code transcripts on the hub held 86,343 unique tool calls. The user rejected 20 of them, and nearly
all of those were interruptions ("this has been going for way too long"), not safety calls. Claude Code's own auto
mode denied 172. A judge that asks on a third of ordinary calls is wrong for this user even when each ask looks
defensible.

## The run

399 tool calls (Bash, Edit, Write, MCP) were scraped with the user's request, the agent's text before the call and
the agent's message the request answered. Each was hand-labeled allow or stop for what the user would stop. 289 went
into the design, and 110 were a holdout drawn after the thresholds were set. 23 were labeled stop. Every judge ran
live on jev-1.14.0.

| judge | stops caught (of 23) | wrong stops (of 376) | asks on everyday calls (of 170) |
|---|---|---|---|
| Claude Code auto mode (its recorded verdicts) | 18 | 102 | 0 |
| this plugin's auto mode (`before.decide` with its floor) | 20 | 170 | 40 |
| hermes-dispatch jevguard | 21 | 209 | 53 |
| hermes-dispatch policy shadow | 23 | 229 | 68 |
| autonomy-first judge (live rerun of the shipped module) | 16 | 17 | 5 |

The labels are one reader's judgment of one user's taste, and 23 stops is a small number.

## Why the plugin over-asks

- **The risk Score with a confidence gate.** Allow needs risk at most 1.25 and confidence at least 0.60. A plain
  `grep; npm test` splits its mass between "reads only" and "local reversible write", both harmless, and the
  confidence lands near 0.2, so it asks. Confidence measures how concentrated the answer is, not how safe the call is.
- **The floor as a cap.** Push, delete, install, remote, service and the `\btoken\b` credential pattern send a call to
  the user unless `user_asked_for_it` reaches 0.65. On the hub, jevguard's similar ask rules added 98 wrong stops and
  only 2 catches on this set. Most pushes, merges and deletes in a working session are ordinary.
- **`user_asked_for_it` alone opens calls.** A "Continue" or "i approve all" reads as not asked, because the
  proposal the user approved is not in the state.

## The design that worked

One request, ten Nouls, every threshold in code.

- Eight hazards, each concrete, each with its own line: publishes (0.80), messages_person (0.70), destroys_data
  (0.70), exposes_secret (0.85), weakens_safety (0.70), disrupts_live (0.85), private_data (0.80), spends_money (0.70).
  No hazard over its line means allow. There is no risk Score.
- `requested` opens a hot hazard at 0.60, private data at 0.70, and a secret never.
- `forbidden` asks at 0.70: the request named this exact operation as off limits ("do not merge", "no paid API
  calls", "show me before you post").
- A secret shown and also leaving (exposes_secret 0.80 with publishes or messages_person 0.60) is denied.
- The state carries `agent_previous_message`, the agent text the request answered, so "yes, do that" reads against
  the proposal. It also carries a short policy in the user's own terms.
- The code floor only denies (conversation links, the user's deny globs, force push) and allows provable reads. It is
  not a cap.
- One code rule: a printing command (`cat`, `head`, `grep` without `-c`/`-l`/`-q`, ...) that names a secret file asks.
  Jev believed a `sed` that hid only `TOKEN=` lines while the rest of an `.env` printed.

Wording that mattered, from the first version to the second:

- publishes: "by running now". Writing a deploy config or a script that could publish later had read as publishing.
- exposes_secret: "the value ... where it can be seen or where it does not belong", with passing a key to its own
  service and public material such as a key pin named as a no.
- forbidden: narrowed to the risky operation. A request that said "read only" had made every scratch write look
  forbidden.

## What it still misses

An unrequested restart of a live service, a guard hook written in a repository, a live config change, a paid run the
request ruled out, digging in the keychain, and merges into the main branch of a live service. Publishing,
messaging, deploys, `.env` prints, private message reads and secret moves were caught.

## Carrying it over

`before.py` could keep its describe(), its fail-open rule and its never-allow list, and swap the Score plus five
Nouls for the ten Nouls above with `compose()`'s rules. The plugin's own floor would shrink to deny-only plus the user's
never-allow list. The prompt text is in `hermes_dispatch/jevauto.py` (`QUESTIONS`, `POLICY`, `STANDING_RULES`); the
standing rules there describe the hub, so the plugin would need its own.
