# jev_client.py - the TypeSafe System One (Jev) client, stdlib only.
# vendored from a shared copy, 2026-10-11. keep it a copy, not an import.

import http.client
import json
import math
import os
import re
import socket
import threading
import time
import urllib.parse

API_URL = "https://api.typesafe.ai/v1/systemone"

# one retry on 429/529 and how long it waits first. named so a caller working
# to its own budget can price a whole ask()
ATTEMPTS = 2
BACKOFF_SECONDS = 2.0

# the slack another attempt needs before the retry is worth starting
MIN_ATTEMPT_SECONDS = 0.5

# nothing real comes back anywhere near this. a body past it is a server
# misbehaving, not an answer
MAX_BYTES = 1 << 20

# how much of an error body is kept once it is redacted
MAX_ERROR_CHARS = 400

# never let a socket operation arm with 0, which means non-blocking
_MIN_SOCKET_TIMEOUT = 0.05

_REDACTIONS = (
    # "Authorization": "Bearer sk-..." and Authorization: Bearer sk-...
    (re.compile(r"(?i)authorization\"?\s*[:=]\s*\"?[^\"\r\n,}]*"),
     "authorization=<redacted>"),
    # a bare bearer token anywhere else in the body
    (re.compile(r"(?i)bearer\s+\S+"), "Bearer <redacted>"),
    # the env assignment, as it shows up in echoed shell lines
    (re.compile(r"(?i)TYPESAFE_API_KEY\s*=\s*\S*"), "TYPESAFE_API_KEY=<redacted>"),
)

# jev reads dates and counts badly, so free text
# is stripped of them before it goes into state or criteria
_SCRUB = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+)?"
    r"|\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b"
    r"|\b\d{1,2}:\d{2}\s*(?:am|pm)?"
    r"|\b\d{1,2}\s*(?:am|pm)\b"
    r"|\b\d+(?:\.\d+)?\s*(?:pts?|points?|%)"
    r"|\b(?:19|20)\d{2}\b"
    r"|\b(?:mon|tues?|wed(?:nes)?|thur?s?|fri|sat(?:ur)?|sun)(?:day)?\b"
    r"|\b(?:jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{1,2}(?:st|nd|rd|th)?\b"
    r"|\bmay\s+\d{1,2}(?:st|nd|rd|th)?\b",
    re.I)


def scrub(text):
    # drops dates, clock times, point counts and bare years out of free text
    return " ".join(_SCRUB.sub(" ", text or "").split())


def redact(text):
    # an error body can echo the request back, auth header and all, and the
    # body lands on JevError and in logs. strip it, then truncate
    text = text if isinstance(text, str) else str(text or "")
    key = os.environ.get("TYPESAFE_API_KEY")
    if key and len(key) >= 8:
        text = text.replace(key, "<redacted>")
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    if len(text) > MAX_ERROR_CHARS:
        text = text[:MAX_ERROR_CHARS] + "...[truncated]"
    return text


class JevError(Exception):
    # carries http status + redacted response body. every failure in this
    # module leaves as one of these, so callers catch exactly one type
    def __init__(self, status, body):
        self.status = status
        self.body = redact(body)
        super().__init__("jev http %s: %s" % (status, self.body))


def noul(instructions, criteria=None):
    q = {"type": "noul", "instructions": instructions}
    if criteria:
        q["criteria"] = criteria
    return q


def choice(instructions, criteria):
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions, levels):
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


def _url():
    # test-only override, read at call time so a test can patch either the env
    # or the module constant
    return os.environ.get("JEV_API_URL") or API_URL


def _left(end, what):
    left = end - time.monotonic()
    if left <= 0:
        raise JevError(0, "deadline reached %s" % what)
    return left


def _arm(conn, end, what):
    # re-arm the live socket to the time actually left. urllib's and
    # http.client's timeout is per socket operation, not a budget, so without
    # this a slow drip resets the clock on every byte
    left = _left(end, what)
    sock = getattr(conn, "sock", None)
    if sock is not None:
        try:
            sock.settimeout(max(_MIN_SOCKET_TIMEOUT, left))
        except OSError:
            pass
    return left


def _hangup(conn):
    # shutdown, not close: it wakes a recv that is already blocked
    sock = getattr(conn, "sock", None)
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def _read_capped(conn, resp, end):
    chunks, total = [], 0
    while True:
        _arm(conn, end, "while reading the response")
        chunk = resp.read1(8192) if hasattr(resp, "read1") else resp.read(8192)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_BYTES:
            raise JevError(0, "response over %d bytes" % MAX_BYTES)
        chunks.append(chunk)
    # http.client's read(amt) does not raise on a short body, it just stops.
    # a content-length still owing bytes means the peer hung up mid-body
    owing = getattr(resp, "length", None)
    if owing:
        raise JevError(0, "short read: %d bytes still owed" % owing)
    return b"".join(chunks)


def _post(payload, key, end, out):
    # runs in a worker thread. the thread is what bounds a hang no socket
    # timeout reaches - a stuck dns lookup, mostly, since getaddrinfo ignores
    # the socket timeout entirely (jevguard_hook covers the same hole with
    # SIGALRM, which is unavailable off the main thread). the timer below is
    # the backstop that kills an abandoned thread's socket instead of leaking it
    conn = timer = None
    try:
        parts = urllib.parse.urlsplit(_url())
        cls = (http.client.HTTPSConnection if parts.scheme == "https"
               else http.client.HTTPConnection)
        left = _left(end, "before connecting")
        conn = cls(parts.hostname, parts.port,
                   timeout=max(_MIN_SOCKET_TIMEOUT, left))
        timer = threading.Timer(left, _hangup, [conn])
        timer.daemon = True
        timer.start()
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        conn.request("POST", path, body=payload, headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        _arm(conn, end, "while waiting for the response")
        resp = conn.getresponse()
        raw = _read_capped(conn, resp, end)
        out["result"] = (resp.status, resp.getheader("Retry-After"), raw)
    except JevError as e:
        out["err"] = e
    except (OSError, http.client.HTTPException) as e:
        # socket.timeout, ConnectionResetError, RemoteDisconnected,
        # IncompleteRead, ssl.SSLError - all land here, none are URLError
        out["err"] = JevError(0, "%s: %s" % (type(e).__name__, e))
    except Exception as e:
        out["err"] = JevError(0, "%s: %s" % (type(e).__name__, e))
    finally:
        if timer is not None:
            timer.cancel()
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _attempt(payload, key, end):
    # http.client follows nothing and consults no proxy, which is the point:
    # urllib keeps the Authorization header across a redirect to another host,
    # so a 3xx off api.typesafe.ai would hand TYPESAFE_API_KEY to whoever it
    # points at. here a 3xx is just a status, and ask() turns it into a JevError
    out = {}
    worker = threading.Thread(target=_post, args=(payload, key, end, out),
                              daemon=True)
    worker.start()
    worker.join(max(0.0, end - time.monotonic()))
    if worker.is_alive():
        # abandoned: daemon, and the timer has already shut its socket down
        raise JevError(0, "deadline reached with no response")
    if "err" in out:
        raise out["err"]
    if "result" not in out:
        raise JevError(0, "no answer")
    return out["result"]


def _retry_after(header, end):
    # honour retry-after in seconds when it is there and it fits. the http-date
    # form is ignored on purpose; it is never worth a clock-skew bug here
    wait = BACKOFF_SECONDS
    if header:
        try:
            asked = float(str(header).strip())
        except ValueError:
            asked = None
        if asked is not None and asked >= 0:
            wait = asked
    if end - time.monotonic() > wait + MIN_ATTEMPT_SECONDS:
        return wait
    return None


def ask(state, questions, model="jev-latest", deadline=20.0):
    # deadline is one wall-clock budget for the whole call, retry and backoff
    # included. every failure comes out as JevError
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        raise JevError(0, "TYPESAFE_API_KEY not in env")
    try:
        payload = json.dumps(
            {"state": state, "model": model, "questions": questions}).encode()
    except (TypeError, ValueError) as e:
        raise JevError(0, "request is not json-serialisable: %s" % e)
    end = time.monotonic() + float(deadline)

    for attempt in range(ATTEMPTS):
        _left(end, "before the request")
        status, retry_after, raw = _attempt(payload, key, end)

        if 300 <= status < 400:
            # never followed. a redirect off the api host would leak the key
            raise JevError(status, "refused to follow a redirect")

        if not (200 <= status < 300):
            body = raw.decode("utf-8", "replace")
            # one retry on 429/529, and only when the backoff plus another
            # attempt still fit inside the deadline
            if (status in (429, 529) and attempt + 1 < ATTEMPTS):
                wait = _retry_after(retry_after, end)
                if wait is not None:
                    time.sleep(wait)
                    continue
            raise JevError(status, body)

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise JevError(status, "response was not utf-8: %s" % e)
        try:
            data = json.loads(text)
        except ValueError as e:
            raise JevError(status, "malformed json: %s: %s" % (e, text))
        if not isinstance(data, dict):
            raise JevError(status, "response body was not an object")
        if not isinstance(data.get("answers"), dict):
            raise JevError(status, "no answers object in the response")
        return data

    raise JevError(0, "gave up after %d attempts" % ATTEMPTS)


def answers_of(resp, expected_ids):
    # every caller goes through this, so a wrong-shaped body is a JevError like
    # any other. the ids have to match exactly: a missing answer is a silent
    # default downstream, an extra one means the response is not ours
    if not isinstance(resp, dict) or not isinstance(resp.get("answers"), dict):
        raise JevError(0, "malformed response: no answers object")
    answers = resp["answers"]
    want = set(expected_ids)
    got = set(answers)
    missing = sorted(want - got)
    extra = sorted(got - want)
    if missing or extra:
        raise JevError(0, "answer ids off: missing %s, unexpected %s"
                       % (missing or "none", extra or "none"))
    return answers


def _prob(value, what):
    # a real number in [0, 1]. bool is an int in python, and nan/inf fail
    # isfinite, so neither slips through as a probability
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JevError(0, "malformed %s: not a number" % what)
    f = float(value)
    if not math.isfinite(f) or not (0.0 <= f <= 1.0):
        raise JevError(0, "malformed %s: %r out of [0,1]" % (what, value))
    return f


def _answer(ans, kind):
    if not isinstance(ans, dict):
        raise JevError(0, "malformed %s answer: not an object" % kind)
    return ans


def _confidence(ans):
    # only choice and score reach here, and docs.typesafe.ai/confidence.md says
    # it "returns it on every Choice and Score answer". a reply without one is a
    # malformed response, not an unsure answer - defaulting it to 0.0 would
    # invent a number the server never sent and read as a real low-confidence
    # verdict downstream
    if "confidence" not in ans:
        raise JevError(0, "answer carries no confidence")
    return _prob(ans["confidence"], "confidence")


def _probabilities(ans, allowed_keys):
    probs = ans.get("probabilities")
    if probs is None:
        return None
    if not isinstance(probs, dict):
        raise JevError(0, "malformed probabilities: not an object")
    stray = sorted(set(probs) - set(allowed_keys))
    if stray:
        raise JevError(0, "probabilities over unknown keys: %s" % stray)
    return {k: _prob(v, "probability for %r" % k) for k, v in probs.items()}


def noul_of(ans):
    # -> float in [0, 1]
    return _prob(_answer(ans, "noul").get("noul"), "noul")


def choice_of(ans, allowed):
    # -> (choice, confidence). `allowed` is the criteria dict or its keys
    ans = _answer(ans, "choice")
    allowed = list(allowed)
    pick = ans.get("choice")
    if not isinstance(pick, str):
        raise JevError(0, "malformed choice: not a string")
    if pick not in allowed:
        raise JevError(0, "choice %r is not one of %s" % (pick, sorted(allowed)))
    _probabilities(ans, allowed)
    return pick, _confidence(ans)


def probabilities_of(ans, allowed):
    # -> {option: probability} for a choice. with no probabilities, the pick gets 1.0
    pick, _ = choice_of(ans, allowed)
    probs = _probabilities(ans, list(allowed))
    return probs if probs else {pick: 1.0}


def score_of(ans, levels):
    # -> (score, confidence). levels is the rubric list handed to score().
    # the score spans 0 .. len(levels)-1 and may fall between integers
    ans = _answer(ans, "score")
    levels = list(levels)
    if len(levels) < 2:
        raise JevError(0, "score needs at least two levels")
    raw = ans.get("score")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise JevError(0, "malformed score: not a number")
    value = float(raw)
    top = float(len(levels) - 1)
    if not math.isfinite(value) or not (0.0 <= value <= top):
        raise JevError(0, "score %r out of [0,%g]" % (raw, top))
    _probabilities(ans, [str(i) for i in range(len(levels))])
    return value, _confidence(ans)
