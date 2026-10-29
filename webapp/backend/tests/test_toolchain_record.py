"""Toolchain record: every measured run carries a hash of each harness source
file and the version of each tool its language uses (runner side, in
examples/perfarena_handler.py), and the platform stores it on the Run and shows
it with the attempt. It is a record only: it does not enter the measurement
profile, so a change does not split a ranking."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlmodel import Session, select

from conftest import ADMIN
from app import dispatch
from app.db import engine
from app.models import Run
from replica_fakes import make_oracles, make_submission, real_mode, runners  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "examples"))
import perfarena_handler as h  # noqa: E402


def _tree(tmp_path: Path, monkeypatch) -> Path:
    """An Energy-Languages root with one JavaScript LeetCode cell and a fake
    `node` on PATH that prints its version from a file."""
    root = tmp_path / "el"
    (root / "selection").mkdir(parents=True)
    (root / "perfarena.mk").write_text("# mk\n")
    (root / "selection" / "run_harness.sh").write_text("#!/bin/sh\n")
    (root / "selection" / "harness.js").write_text("// v1\n")
    (root / "selection" / "harness_node_common.js").write_text("// loop\n")
    cell = root / "JavaScript" / "leetcode" / "two-sum"
    cell.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "node_version").write_text("v20.20.2")
    node = bin_dir / "node"
    node.write_text(f"#!/bin/sh\ncat {tmp_path / 'node_version'}\n")
    node.chmod(0o755)
    monkeypatch.setenv("PERFARENA_ROOT", str(root))
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setattr(h, "_VERSION_CACHE", {})
    return cell


def test_record_has_harness_hashes_and_tool_versions(tmp_path, monkeypatch):
    cell = _tree(tmp_path, monkeypatch)
    tc = h._toolchain(cell, "two-sum", "javascript")
    assert set(tc["harness"]) == {"selection/run_harness.sh", "selection/harness.js",
                                  "selection/harness_node_common.js", "perfarena_handler.py"}
    assert tc["tools"]["node"] == "v20.20.2"
    # A changed harness file changes only its own hash.
    before = tc["harness"]
    (cell.parents[2] / "selection" / "harness.js").write_text("// v2\n")
    after = h._toolchain(cell, "two-sum", "javascript")["harness"]
    assert after["selection/harness.js"] != before["selection/harness.js"]
    assert after["selection/run_harness.sh"] == before["selection/run_harness.sh"]


def test_an_upgraded_tool_is_seen_on_the_next_run(tmp_path, monkeypatch):
    cell = _tree(tmp_path, monkeypatch)
    assert h._toolchain(cell, "two-sum", "javascript")["tools"]["node"] == "v20.20.2"
    # Upgrading replaces the binary, so its size or mtime changes.
    (tmp_path / "node_version").write_text("v22.1.0")
    node = tmp_path / "bin" / "node"
    node.write_text(node.read_text() + "# upgraded\n")
    assert h._toolchain(cell, "two-sum", "javascript")["tools"]["node"] == "v22.1.0"


def test_platform_stores_the_record_on_the_run(real_mode, runners, client):
    perf = runners("perf")
    record = {"harness": {"selection/harness.js": "abc"}, "tools": {"node": "v20.20.2"}}
    plain = perf.run
    perf.run = lambda: {**plain(), "toolchain": record}
    hid, _ = make_oracles(perf)
    sid, (aid,) = make_submission(hid)
    dispatch.dispatch_submission(sid)
    with Session(engine) as s:
        runs = s.exec(select(Run).where(Run.attempt_id == aid)).all()
        assert runs and all(r.toolchain == record for r in runs)
    shown = client.get(f"/api/submissions/{sid}/attempts", headers=ADMIN).json()
    assert all(r["toolchain"] == record for r in shown[0]["runs"])
