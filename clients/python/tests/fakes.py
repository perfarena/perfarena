"""Fake HTTP services for SDK tests: a runner that judges code with a
callable, and an Ollama-compatible LLM server. Both are real HTTP servers on
127.0.0.1, so every SDK and script code path runs over the wire."""
from __future__ import annotations

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable


class _Server:
    def __init__(self, handler_cls):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _handler(owner) -> type[BaseHTTPRequestHandler]:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code: int, payload) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            owner.handle(self, "GET", None)

        def do_POST(self):
            n = int(self.headers.get("content-length") or 0)
            owner.handle(self, "POST", json.loads(self.rfile.read(n) or b"{}"))
    return H


class FakeRunner(_Server):
    """Runner protocol (/healthz, /validate, /measure). `judge(code, slug)`
    returns the status; `runtime(code)` the runtime in ms."""

    def __init__(self, judge: Callable[[str, str], str] | None = None,
                 runtime: Callable[[str], float] | None = None):
        self.judge = judge or (lambda code, slug: "Accepted")
        self.runtime = runtime or (lambda code: 10.0)
        self.down = False
        self.calls: list[tuple[str, dict]] = []
        self.machine = f"box-{uuid.uuid4().hex[:6]}"
        super().__init__(_handler(self))

    def handle(self, h, method, body):
        if self.down:
            return h.send(503, {"error": "down"})
        if method == "GET":
            return h.send(200, {"ok": True, "hostname": self.machine})
        self.calls.append((h.path, body))
        code, slug = body.get("code", ""), body.get("title_slug", "")
        status = self.judge(code, slug)
        if h.path == "/validate":
            return h.send(200, {"verdict": {"status_msg": status, "total_correct": 3,
                                            "total_testcases": 3}})
        runs = [{"status_msg": status, "runtime_ms": self.runtime(code), "memory_mb": 5.0,
                 "runtime_energy_j": 1.0, "energy_tool": "rapl", "config": {"budget_s": 1},
                 "machine": {"name": self.machine, "hostname": self.machine}}
                for _ in range(int(body.get("repeats", 1)))]
        h.send(200, {"runs": runs})

    def n(self, path: str) -> int:
        return sum(1 for p, _ in self.calls if p == path)


class FakeOllama(_Server):
    """Ollama /api/chat + /api/tags. `answer(messages, model)` returns the
    assistant content; `thinking` (optional) the reasoning channel."""

    def __init__(self, answer: Callable[[list, str], str], *,
                 thinking: Callable[[list, str], str | None] | None = None,
                 digest: str = "sha256:feedface", delay_s: float = 0.0,
                 models: tuple[str, ...] = ()):
        self.answer, self.thinking, self.digest, self.delay_s = answer, thinking, digest, delay_s
        self.models = models
        self.requests: list[dict] = []
        super().__init__(_handler(self))

    def handle(self, h, method, body):
        if method == "GET":                    # /api/tags: every model has the digest
            names = {r["model"] for r in self.requests} | set(self.models)
            return h.send(200, {"models": [{"name": m, "digest": self.digest}
                                           for m in sorted(names)]})
        self.requests.append(body)
        if self.delay_s:
            time.sleep(self.delay_s)
        msgs, model = body["messages"], body["model"]
        msg = {"role": "assistant", "content": self.answer(msgs, model)}
        if self.thinking and (t := self.thinking(msgs, model)):
            msg["thinking"] = t
        h.send(200, {"model": model, "message": msg, "done": True, "done_reason": "stop",
                     "eval_count": 42, "prompt_eval_count": 7})


def fenced(code: str, lang: str = "python") -> str:
    return f"Here you go.\n```{lang}\n{code}\n```\n"


GOOD = "class Solution:\n    def solve(self, x):\n        return sum(range(x))\n"
BAD = ("class Solution:\n    def solve(self, x):\n        total = 0\n"
       "        for i in range(x):\n            total -= i\n        return -1\n")


def judge_by_marker(code: str, slug: str) -> str:
    """Accepted unless the code returns -1 (the BAD solution)."""
    return "Wrong Answer" if "return -1" in code else "Accepted"
