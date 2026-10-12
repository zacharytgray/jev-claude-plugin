"""the one place hooks reach Jev. tests swap `client` for a stub.

the key comes from TYPESAFE_API_KEY in the environment and nowhere else. with
no key, every call fails, and every hook fails open.
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

from . import config, jev_client

MODEL = "jev-1.13.0"

# per request. the paper's recorded p95 sets it (see the README)
DEADLINE_SECONDS = 4.0
# a whole hook, every window included
BUDGET_SECONDS = 20.0
WORKERS = 16

JevError = jev_client.JevError


class _Http:
    def ask(self, state, questions, deadline):
        return jev_client.ask(state, questions, model=MODEL, deadline=deadline)


client = _Http()


def has_key():
    return bool(os.environ.get("TYPESAFE_API_KEY"))


def ask(state, questions, deadline=DEADLINE_SECONDS):
    resp = client.ask(state, questions, deadline)
    return jev_client.answers_of(resp, questions)


def ask_many(requests, deadline=DEADLINE_SECONDS, budget=BUDGET_SECONDS):
    """[(state, questions)] -> [answers], in order. any failure raises JevError:
    a filter with a hole in it is worse than no filter, so callers fail open."""
    if not requests:
        return []
    end = time.monotonic() + budget
    with ThreadPoolExecutor(max_workers=min(WORKERS, len(requests))) as pool:
        futures = [pool.submit(ask, s, q, deadline) for s, q in requests]
        out = []
        for fut in futures:
            left = end - time.monotonic()
            if left <= 0:
                raise JevError(0, "hook budget spent")
            try:
                out.append(fut.result(timeout=left))
            except JevError:
                raise
            except Exception as exc:
                raise JevError(0, type(exc).__name__)
        return out


def noul(instructions, true=None, false=None):
    crit = {"true": true, "false": false} if true and false else None
    return jev_client.noul(instructions, crit)


noul_of = jev_client.noul_of
score_of = jev_client.score_of
choice_of = jev_client.choice_of
score = jev_client.score
choice = jev_client.choice


def log(event, **fields):
    """one json line per hook decision. never the key, never whole outputs."""
    try:
        os.makedirs(config.home(), exist_ok=True)
        fields.update(event=event, t=round(time.time(), 3))
        with open(os.path.join(config.home(), "log.jsonl"), "a") as f:
            f.write(json.dumps(fields, default=str) + "\n")
    except OSError:
        pass
