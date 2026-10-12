"""end: is each claim backed (Stop). blocks once, never twice in a row.

code splits the final reply into claims and lays out this turn's tool results
as evidence; jev answers one yes/no per claim, each claim named by its key.
"""

import re

from . import config, jev, transcript

BACKED_CUT = 0.2
MAX_CLAIMS = 12
EVIDENCE_ITEMS = 12
EVIDENCE_EACH = 4000
EVIDENCE_TOTAL = 48000

# a sentence that states a result: a past or present outcome, a count, a pass
_RESULT = re.compile(
    r"\b(pass(es|ed)?|fail(s|ed)?|fixed|works?|working|built|builds|added|created|removed|"
    r"deleted|updated|changed|renamed|moved|wrote|written|ran|runs|returns?|returned|succeeded|"
    r"verified|confirmed|found|shows?|showed|is now|are now|now (has|have|uses?)|all \d+|"
    r"\d+ (of|tests?|files?|errors?|warnings?))\b", re.I)
_NOT_CLAIM = re.compile(
    r"^(if|when|should|could|would|do you|shall|let me|next|i will|i'll|i can|you can|"
    r"to |please|want me|consider|maybe|perhaps)\b", re.I)
_CHECK = re.compile(
    r"\b(pytest|unittest|npm (run )?test|yarn test|pnpm test|go test|cargo test|make (test|check)|"
    r"tox|ruff|mypy|eslint|tsc|swift test|xcodebuild test|verify|check\.py|run_tests)\b")
_FAILURE = re.compile(r"\b(\d+ failed|FAILED|Error|error:|Traceback)\b")


def claims(reply):
    text = re.sub(r"```.*?```", " ", reply or "", flags=re.S)
    out = []
    for raw in re.split(r"(?<=[.!?])\s+|\n+", text):
        s = raw.strip().lstrip("-*0123456789.) ").strip()
        if len(s) < 12 or s.endswith("?") or s.endswith(":"):
            continue
        if _NOT_CLAIM.match(s) or not _RESULT.search(s):
            continue
        out.append(s[:400])
        if len(out) >= MAX_CLAIMS:
            break
    return out


def _trim(text, cap=EVIDENCE_EACH):
    if len(text) <= cap:
        return text
    half = cap // 2
    return text[:half] + "\n[... %d characters cut ...]\n" % (len(text) - cap) + text[-half:]


def evidence(results):
    """newest results first until the budget is spent, then put back in order."""
    picked, total = [], 0
    for name, inp, text in reversed(results[-EVIDENCE_ITEMS * 2:]):
        call = inp.get("command") or inp.get("file_path") or inp.get("pattern") or inp.get("url") or ""
        body = "%s %s\n%s" % (name, str(call)[:300], _trim(text or ""))
        if total + len(body) > EVIDENCE_TOTAL:
            break
        picked.append(body)
        total += len(body)
        if len(picked) >= EVIDENCE_ITEMS:
            break
    picked.reverse()
    return {"r%d" % (i + 1): b for i, b in enumerate(picked)}


def passing_check(results):
    """the turn's last shell call ran a check and it shows no failure."""
    for name, inp, text in reversed(results):
        if name == "Bash":
            return bool(_CHECK.search(inp.get("command", ""))) and not _FAILURE.search(text or "")
    return False


QUESTION = (
    "Is the claim `claims.{key}` supported by something in `evidence`?",
    "A tool result in evidence shows what the claim says.",
    "Nothing in evidence shows it. Missing evidence counts as not supported, not as contradicted.",
)


def handle(event):
    cfg = config.load()
    if not config.on(cfg, "end.claims_backed"):
        return None
    if event.get("stop_hook_active"):
        return None
    reply = event.get("last_assistant_message") or ""
    found = claims(reply)
    if not found:
        return None
    items = transcript.entries(event.get("transcript_path"))
    results = transcript.turn_results(items)
    if not results:
        # nothing ran this turn, so there is nothing to check a claim against
        return None
    if passing_check(results):
        jev.log("end", skipped="passing check")
        return None
    if not jev.has_key():
        return {"systemMessage": "Jev claim check skipped (no TYPESAFE_API_KEY)."}
    keyed = {"c%d" % (i + 1): c for i, c in enumerate(found)}
    state = {"request": transcript.saved_request(event.get("session_id")) or transcript.last_request(items),
             "evidence": evidence(results), "claims": keyed}
    qs = {"backed_" + k: jev.noul(QUESTION[0].format(key=k), true=QUESTION[1], false=QUESTION[2])
          for k in keyed}
    try:
        answers = jev.ask(state, qs)
        scores = {k: jev.noul_of(answers["backed_" + k]) for k in keyed}
    except jev.JevError as exc:
        jev.log("end", fail_open=str(exc.status or exc.body))
        return {"systemMessage": "Jev claim check could not run (jev %s)." % (exc.status or "error")}
    weak = [k for k, v in scores.items() if v < BACKED_CUT]
    jev.log("end", claims=len(keyed), weak=len(weak), scores={k: round(v, 3) for k, v in scores.items()})
    if not weak:
        return None
    listed = " ".join('"%s"' % keyed[k] for k in weak)
    return {"decision": "block",
            "reason": ("No tool result in this turn shows these claims: %s Check them with a tool, "
                       "or say plainly that they are not verified. This check runs once." % listed)}
