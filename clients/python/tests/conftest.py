"""SDK tests run against the REAL backend (webapp/backend) in-process, on a
throw-away SQLite DB, with real dispatch to fake runners. The environment is
set before the backend is imported."""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "webapp" / "backend"
EXAMPLES = REPO / "webapp" / "examples"
_TMP = Path(tempfile.mkdtemp(prefix="perfarena-sdk-test-"))

os.environ.update({
    "ARENA_DB": str(_TMP / "arena.db"), "ARENA_DISPATCH_ENABLED": "1",
    "ARENA_AUTO_REFRESH_BASELINES": "0", "ARENA_IP_RATE_LIMIT_ENABLED": "0",
    "ARENA_SUBMIT_COOLDOWN_S": "0", "ARENA_SUBMIT_DAILY_CAP": "100000",
    "ARENA_SESSION_SECRET": "test", "ARENA_REPLICA_WAIT_S": "10",
    "ARENA_CORRECTNESS_RETRIES": "0",
    "PERFARENA_PROFILES": str(_TMP / "no-profiles.toml"),
})
for k in ("PERFARENA_BASE_URL", "PERFARENA_API_KEY", "ARENA_BASE_URL", "ARENA_API_KEY",
          "PERFARENA_PROFILE"):
    os.environ.pop(k, None)
sys.path[:0] = [str(BACKEND), str(EXAMPLES)]

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, select       # noqa: E402

from app.auth import hash_api_key           # noqa: E402
from app.db import engine, init_db          # noqa: E402
from app.main import _seed_default_competitions, _seed_default_prompts, app  # noqa: E402
from app.models import (                    # noqa: E402
    Competition, CorrectnessOracle, Harness, Problem, User, UserRole, UserStatus,
)

from fakes import FakeRunner, judge_by_marker  # noqa: E402
from perfarena import Arena, Settings          # noqa: E402

ADMIN_KEY = "sdk-test-admin-key"
USER_KEY = "sdk-test-user-key"
CLBG_CONTENT = "# Spin\n\nPrint N.\n\n## Invocation\n\n`<program> N`\n"
PROBLEMS = [
    (1, "two-sum", "Easy"), (2, "add-digits", "Easy"), (3, "24-game", "Hard"),
    (4, "valid-parentheses", "Easy"), (5, "reverse-integer", "Medium"),
]


@pytest.fixture(scope="session", autouse=True)
def backend():
    init_db()
    _seed_default_prompts()
    with Session(engine) as s:
        for ext, slug, lvl in PROBLEMS:
            s.add(Problem(external_id=ext, title_slug=slug, title=slug.title(), level=lvl,
                          tags="Array", content=f"# {slug}\n\nSolve **{slug}**.",
                          hints='["think about it", "use a loop"]',
                          code_snippets={"python3": f"class Solution:\n    def {slug.replace('-', '_')}(self): pass",
                                         "cpp": "class Solution { };"}))
        s.add(Problem(external_id=99, title_slug="spin", title="Spin", level="Medium",
                      tags="", content=CLBG_CONTENT))
        s.add(User(email="admin@sdk.test", display_name="Admin", role=UserRole.admin,
                   status=UserStatus.approved, api_key=hash_api_key(ADMIN_KEY)))
        s.add(User(email="user@sdk.test", display_name="User", role=UserRole.user,
                   status=UserStatus.approved, api_key=hash_api_key(USER_KEY)))
        s.commit()
    _seed_default_competitions()
    yield


def make_oracles(runner: FakeRunner, *, correctness: bool = True,
                 harness_policy: dict | None = None) -> tuple[str, str | None]:
    """A harness (and correctness oracle) on the fake runner, allowed on
    leetcode-99-perf."""
    tag = uuid.uuid4().hex[:6]
    with Session(engine) as s:
        cfg = {"endpoint": runner.url, "timeout_s": 30, "repeats": 2}
        if harness_policy:
            cfg["replicas"] = harness_policy
        h = Harness(slug=f"h-{tag}", name="fake harness", kind="arena-local", config=cfg)
        s.add(h)
        o = None
        if correctness:
            o = CorrectnessOracle(slug=f"o-{tag}", name="fake oracle", kind="arena-local",
                                  config={"endpoint": runner.url, "timeout_s": 30})
            s.add(o)
        comp = s.exec(select(Competition).where(Competition.slug == "leetcode-99-perf")).one()
        comp.compatible_harness_slugs = [*(comp.compatible_harness_slugs or []), h.slug]
        s.add(comp)
        s.commit()
        return h.slug, (o.slug if o else None)


@pytest.fixture
def runner():
    r = FakeRunner(judge=judge_by_marker)
    yield r
    r.stop()


@pytest.fixture
def oracles(runner):
    return make_oracles(runner)


@pytest.fixture
def arena() -> Arena:
    return Arena(settings=Settings(base_url="http://testserver", api_key=ADMIN_KEY),
                 http=TestClient(app))


@pytest.fixture
def user_arena() -> Arena:
    return Arena(settings=Settings(base_url="http://testserver", api_key=USER_KEY),
                 http=TestClient(app))


@pytest.fixture(scope="session")
def live_url():
    """The backend served by uvicorn on a free port (for code that opens its
    own connections: the CLI, the client scripts)."""
    import uvicorn
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="warning", lifespan="off"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(timeout=5)
