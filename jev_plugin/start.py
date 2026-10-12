"""start: effort, model and skill hints (UserPromptSubmit).

no claude code hook can set the effort or the model for a request (hooks docs,
read 2026-10-11: UserPromptSubmit can only add context or block). so effort
and model are suggestions shown to the user, and skills are a hint to the
agent. one jev request on the user's own words, never agent text.
"""

import glob
import os
import re

from . import config, jev, transcript

EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"]
EFFORT_DESCRIPTIONS = [
    "low: a lookup, a one-line change, or a question with a direct answer",
    "medium: a small change in one place, with a clear goal",
    "high: a change across several files, or a bug whose cause is not yet known",
    "xhigh: a design question or a hard bug with several plausible causes",
    "max: a long, open-ended task where a wrong plan wastes hours",
]
DEFAULT_EFFORT = "high"
EFFORT_CONFIDENCE_MIN = 0.5
MODEL_CONFIDENCE_MIN = 0.6
SKILL_FLOOR = 0.3
MAX_SKILLS = 24

MODELS = {
    "haiku": "Claude Haiku: the fastest and cheapest. Good for lookups, small edits and short questions.",
    "sonnet": "Claude Sonnet: balanced speed and strength. Good for most coding work.",
    "opus": "Claude Opus: the strongest and the most expensive. For hard, long or unclear work.",
}
STRONGEST = "opus"


def _description(head):
    """the frontmatter description, also when it is a folded or literal block (> or |)."""
    m = re.search(r"^description:[ \t]*(.*)$", head, re.M)
    if not m:
        return ""
    first = m.group(1).strip()
    if first and first[0] not in ">|":
        return first.strip("\"'")
    rest = []
    for line in head[m.end():].split("\n")[1:]:
        if line.strip() and not line[:1].isspace():
            break
        rest.append(line.strip())
    return " ".join(x for x in rest if x)


def skills_on_disk(cwd):
    """name -> description, from the user's and the project's SKILL.md files."""
    roots = [os.path.expanduser("~/.claude/skills")]
    if cwd:
        roots.append(os.path.join(cwd, ".claude", "skills"))
    out = {}
    for root in roots:
        for p in sorted(glob.glob(os.path.join(root, "*", "SKILL.md"))):
            try:
                with open(p) as f:
                    head = f.read(4000)
            except OSError:
                continue
            name = os.path.basename(os.path.dirname(p))
            desc = _description(head)
            if desc and name not in out:
                out[name] = desc[:400]
            if len(out) >= MAX_SKILLS:
                return out
    return out


def build(prompt, parts, skills):
    state = {"request": prompt}
    qs = {}
    if "effort" in parts:
        qs["effort"] = jev.score("How much reasoning does `request` need before the first action?",
                                 EFFORT_DESCRIPTIONS)
    if "model" in parts:
        state["models"] = MODELS
        crit = dict(MODELS)
        crit["strongest"] = "The request is hard or unclear: use the strongest model."
        qs["model"] = jev.choice("Which model in `models` should handle `request`?", crit)
    if "skills" in parts:
        state["skills"] = skills
        for i, name in enumerate(skills):
            qs["skill_%d" % i] = jev.noul("Does `request` need the skill `skills.%s`?" % name)
    return state, qs


def decide(answers, parts, skills):
    out = {}
    if "effort" in parts:
        score, conf = jev.score_of(answers["effort"], EFFORT_DESCRIPTIONS)
        idx = min(max(int(round(score)), 0), len(EFFORT_LEVELS) - 1)
        out["effort"] = EFFORT_LEVELS[idx] if conf >= EFFORT_CONFIDENCE_MIN else DEFAULT_EFFORT
    if "model" in parts:
        pick, conf = jev.choice_of(answers["model"], list(MODELS) + ["strongest"])
        out["model"] = pick if (conf >= MODEL_CONFIDENCE_MIN and pick != "strongest") else STRONGEST
    if "skills" in parts:
        out["skills"] = [n for i, n in enumerate(skills)
                         if jev.noul_of(answers["skill_%d" % i]) >= SKILL_FLOOR]
    return out


def handle(event):
    prompt = (event.get("prompt") or "").strip()
    # always remember the request: the other hooks need it, at no jev cost
    try:
        transcript.save_request(event.get("session_id"), prompt)
    except OSError:
        pass
    cfg = config.load()
    parts = [p for p in ("effort", "model", "skills") if config.on(cfg, "start." + p)]
    if not parts or not prompt or prompt.startswith("/"):
        return None
    skills = skills_on_disk(event.get("cwd")) if "skills" in parts else {}
    if "skills" in parts and not skills:
        parts.remove("skills")
        if not parts:
            return None
    if not jev.has_key():
        return {"systemMessage": "Jev start hints are off for this request (no TYPESAFE_API_KEY)."}
    state, qs = build(prompt, parts, skills)
    try:
        answers = jev.ask(state, qs)
        got = decide(answers, parts, skills)
    except jev.JevError as exc:
        jev.log("start", fail_open=str(exc.status or exc.body))
        return {"systemMessage": "Jev start hints failed (jev %s)." % (exc.status or "error")}
    jev.log("start", **got)
    user = []
    if "effort" in got:
        user.append("effort %s" % got["effort"])
    if "model" in got:
        user.append("model %s" % got["model"])
    result = {}
    if user:
        result["systemMessage"] = "Jev suggests for this request: " + ", ".join(user) + "."
    if got.get("skills"):
        result["hookSpecificOutput"] = {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "Skills that may fit this request, a hint from the Jev plugin: %s. "
                                 "Use one only if it fits." % ", ".join(got["skills"]),
        }
    return result or None
