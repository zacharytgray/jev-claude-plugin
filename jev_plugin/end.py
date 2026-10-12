"""end: is each claim backed (Stop). blocks once, never twice in a row.

code splits the final reply into claims and lays out this turn's tool results
as evidence; jev answers one yes/no per claim, each claim named by its key.
"""

import re

from . import config, jev, transcript

BACKED_CUT = 0.1
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


_WORD = re.compile(r"[a-z0-9][a-z0-9_.$%-]{2,}")


_STOP = set("the and for that this with are was were from has have had not but all any can its into than then "
            "there their these those which what when where who will would should could been being also only "
            "such each other more most some our you your they them she his her him".split())


def _words(text):
    return set(_WORD.findall((text or "").lower())) - _STOP


def _trim(text, want=frozenset(), cap=EVIDENCE_EACH):
    """keep the lines that share the most words with the claims, else head and tail.
    head and tail alone cut out a quote from the middle of a long read (round 25, S4)."""
    if len(text) <= cap:
        return text
    lines = text.split("\n")
    if want:
        # rank 3-line spans by claim words, keep the best until the budget is spent
        spans = []
        for i in range(0, len(lines), 3):
            hit = len(_words("\n".join(lines[i:i + 3])) & want)
            if hit:
                spans.append((-hit, i))
        keep, used = set(), 0
        for _, i in sorted(spans):
            size = sum(len(l) + 1 for l in lines[i:i + 3])
            if used + size > cap:
                continue
            keep.update(range(i, min(i + 3, len(lines))))
            used += size
        if keep:
            out, gap = [], 0
            for n, l in enumerate(lines):
                if n in keep:
                    if gap:
                        out.append("[... %d lines cut ...]" % gap)
                        gap = 0
                    out.append(l)
                else:
                    gap += 1
            if gap:
                out.append("[... %d lines cut ...]" % gap)
            return "\n".join(out)
    half = cap // 2
    return text[:half] + "\n[... %d characters cut ...]\n" % (len(text) - cap) + text[-half:]


def evidence(results, claims=()):
    """the results that share the most words with the claims, then the newest, until the
    budget is spent; put back in order."""
    want = frozenset(set().union(*[_words(c) for c in claims])) if claims else frozenset()
    recent = list(enumerate(results))[-EVIDENCE_ITEMS * 2:]
    ranked = sorted(recent, key=lambda r: (-len(_words(r[1][2]) & want), -r[0]))
    picked, total = [], 0
    for idx, (name, inp, text) in ranked:
        call = inp.get("command") or inp.get("file_path") or inp.get("pattern") or inp.get("url") or ""
        body = "%s %s\n%s" % (name, str(call)[:300], _trim(text or "", want))
        if total + len(body) > EVIDENCE_TOTAL:
            continue
        picked.append((idx, body))
        total += len(body)
        if len(picked) >= EVIDENCE_ITEMS:
            break
    picked.sort()
    return {"r%d" % (i + 1): b for i, (_, b) in enumerate(picked)}


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
             "evidence": evidence(results, found), "claims": keyed}
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
