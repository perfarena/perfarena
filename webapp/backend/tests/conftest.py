"""Shared test setup.

Env is configured *before* importing the app so the SQLite engine binds to a
throw-away DB and the fake judge (no ARENA_DISPATCH_ENABLED) is used. Rate
limits are widened so the suite isn't throttled.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="arena-test-"))
os.environ["ARENA_DB"] = str(_TMP / "test.db")
os.environ.pop("ARENA_DISPATCH_ENABLED", None)          # fake judge
os.environ["ARENA_DISPATCH_REPEATS"] = "3"
os.environ["ARENA_SUBMIT_COOLDOWN_S"] = "0"
os.environ["ARENA_SUBMIT_DAILY_CAP"] = "10000"
os.environ["ARENA_REGISTER_IP_HOURLY"] = "10000"
os.environ["ARENA_REGISTER_IP_DAILY"] = "10000"
# Runtime default is now "off" (so a fresh deployment doesn't hide small
# datasets); the eligibility tests want strict-mode behavior so they pin it.
os.environ["ARENA_LEADERBOARD_GATE"] = "on"

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, select       # noqa: E402

from app.db import engine, init_db          # noqa: E402
from app.main import app, _seed_default_prompts   # noqa: E402
from app.models import (                    # noqa: E402
    Problem, User, UserRole, UserStatus,
)

ADMIN_KEY = "arena_test_admin"
USER_KEY = "arena_test_user"

ADMIN = {"Authorization": f"Bearer {ADMIN_KEY}"}
USER = {"Authorization": f"Bearer {USER_KEY}"}

SEED_PROBLEMS = [
    (1, "two-sum", "Two Sum", "Easy", "Array,Hash Table"),
    (2, "add-digits", "Add Digits", "Easy", "Math"),
    (3, "24-game", "24 Game", "Hard", "Array,Math,Backtracking"),
    (4, "valid-parentheses", "Valid Parentheses", "Easy", "String,Stack"),
    (5, "reverse-integer", "Reverse Integer", "Medium", "Math"),
]

GOOD_CODE = (
    "class Solution:\n"
    "    def solve(self, x):\n"
    "        total = 0\n"
    "        for i in range(x):\n"
    "            total += i\n"
    "        return total\n"
)
STUB_CODE = "class Solution:\n    def solve(self, x): ..."
TRACE = "uses an accumulator loop over the range; O(n) time, O(1) space"


@pytest.fixture(scope="session", autouse=True)
def _seed():
    init_db()
    _seed_default_prompts()      # the startup hook isn't called outside a context-manager TestClient
    with Session(engine) as s:
        if not s.exec(select(Problem)).first():
            for ext, slug, title, lvl, tags in SEED_PROBLEMS:
                s.add(Problem(
                    external_id=ext, title_slug=slug, title=title, level=lvl,
                    tags=tags, content=f"# {title}\n\nSolve **{slug}**.", hints=None,
                ))
        if not s.exec(select(User).where(User.api_key == ADMIN_KEY)).first():
            s.add(User(email="admin@test.dev", display_name="Admin",
                       role=UserRole.admin, status=UserStatus.approved,
                       api_key=ADMIN_KEY))
            s.add(User(email="user@test.dev", display_name="Comp User",
                       org="Acme Labs", role=UserRole.user,
                       status=UserStatus.approved, api_key=USER_KEY))
        s.commit()
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def make_pending_user():
    """Create a fresh pending user, return its id (for admin approve/reject)."""
    created: list[int] = []

    def _make(email: str) -> int:
        with Session(engine) as s:
            u = User(email=email, display_name="Pending One",
                     role=UserRole.user, status=UserStatus.pending)
            s.add(u)
            s.commit()
            s.refresh(u)
            created.append(u.id)
            return u.id

    return _make


def solve_captcha(client: TestClient) -> tuple[str, str]:
    """GET a captcha and compute its answer (prompt: 'What is A op B?')."""
    c = client.get("/api/captcha").json()
    a, op, b = c["prompt"].replace("What is ", "").rstrip("?").split()
    ans = int(a) + int(b) if op == "+" else int(a) - int(b)
    return c["token"], str(ans)


def make_submission(client: TestClient, slugs=("two-sum", "add-digits"),
                    headers=None) -> int:
    body = {
        "model_name": "test-model", "model_version": "v1",
        "language": "python3", "prompt_template": "solve the problem cleanly",
        "model_params": {"temperature": 0.0},
        "attempts": [
            {"title_slug": s, "code": GOOD_CODE, "inference_trace": TRACE}
            for s in slugs
        ],
    }
    r = client.post("/api/submissions", json=body, headers=headers or ADMIN)
    assert r.status_code == 202, r.text
    return r.json()["submission_id"]
