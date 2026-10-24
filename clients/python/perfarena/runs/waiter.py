"""Wait for one attempt's verdict. Uses GET /api/attempts/{id}; on an older
server without it, reads the submission's attempt list (matrix_sweep's
_poll_for_attempt rule) and maps it to the same state shape."""
from __future__ import annotations

import time
from typing import Callable

from ..api.resources import Attempts, Submissions, _route_missing
from ..errors import ArenaHTTPError


def state_from_attempt_result(a: dict) -> dict:
    """An /api/submissions/{id}/attempts row as an /api/attempts/{id} state."""
    status = a.get("status")
    if a.get("prefailed_reason"):
        state, verdict = "done", a["prefailed_reason"]
    elif a.get("dispatch_warning") or status == "Dispatch failed":
        state, verdict = "failed", None
    elif status in ("queued", None):
        state, verdict = "queued", None
    elif status == "Pending":
        state, verdict = "running", None
    else:
        state, verdict = "done", status
    return {**a, "state": state, "verdict": verdict,
            "terminal": state in ("done", "failed")}


class AttemptWaiter:
    def __init__(self, attempts: Attempts, submissions: Submissions, *,
                 poll_s: float = 3.0, max_poll_s: float = 10.0,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.attempts, self.submissions = attempts, submissions
        self.poll_s, self.max_poll_s, self.sleep, self.clock = poll_s, max_poll_s, sleep, clock
        self._legacy = False

    def state(self, attempt_id: int, *, submission_id: int | None = None) -> dict | None:
        if not self._legacy:
            try:
                return self.attempts.state(attempt_id)
            except ArenaHTTPError as e:
                if not _route_missing(e) or submission_id is None:
                    raise
                self._legacy = True
        rows = [a for a in self.submissions.attempts(submission_id)
                if a.get("attempt_id") == attempt_id]
        return state_from_attempt_result(rows[0]) if rows else None

    def wait(self, attempt_id: int, timeout_s: float, *,
             submission_id: int | None = None) -> dict | None:
        """The attempt's state once terminal, or None at the timeout."""
        deadline = self.clock() + timeout_s
        delay = self.poll_s
        while True:
            st = self.state(attempt_id, submission_id=submission_id)
            if st is not None and st.get("terminal"):
                return st
            if self.clock() >= deadline:
                return None
            self.sleep(min(delay, max(0.0, deadline - self.clock())))
            delay = min(delay * 1.5, self.max_poll_s)
