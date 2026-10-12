"""a fake Jev endpoint for lab checks, no key and no network.

    python3 tests/fake_jev.py 8765 &
    JEV_API_URL=http://127.0.0.1:8765/v1/systemone TYPESAFE_API_KEY=lab-fake claude --plugin-dir .

relevance is high only for a window holding LAB_KEEP; injection only for one
holding LAB_ATTACK. the harm score is low, so permission prompts are allowed.
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


def answer(qid, q, state):
    chunk = (state.get("chunks") or [""])[0]
    if qid == "relevant":
        return {"noul": 0.9 if "LAB_KEEP" in chunk else 0.02}
    if qid == "injection":
        return {"noul": 0.95 if "LAB_ATTACK" in chunk else 0.01}
    if q.get("type") == "score":
        return {"score": 0.4, "confidence": 0.9}
    if q.get("type") == "choice":
        return {"choice": list(q["criteria"])[0], "confidence": 0.9}
    return {"noul": 0.05}


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        out = {"answers": {k: answer(k, q, body.get("state") or {}) for k, q in body["questions"].items()}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
