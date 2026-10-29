"""Phase 1 compatibility check against a copy of a REAL database.

Opt-in: set ARENA_COMPAT_DB to a SQLite file (e.g. a backup of the deployed
arena.db). The test copies it, upgrades the copy with init_db() in a
subprocess (the new tables, columns, and primary-replica seed), and asserts:

  * every pre-existing table keeps every row and every column value;
  * every MeasurementProfile.config_hash still equals config_hash() of its
    stored energy tool + config, and every Run keeps its profile id;
  * the new columns are NULL on every pre-existing row;
  * each runner oracle with an endpoint got exactly one primary replica.

    ARENA_COMPAT_DB=/path/to/arena-copy.db pytest tests/test_replica_compat.py
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

SRC = os.environ.get("ARENA_COMPAT_DB")
NEW_COLUMNS = {
    "machine": {"machine_class_id"},
    "measurementprofile": {"machine_class_id"},
    "submission": {"machine_class_id"},
    "run": {"replica_id", "machine_id", "toolchain"},
    "attempt": {"dispatch_state", "claimed_by", "lease_until", "not_before",
                "dispatch_tries", "harness_error"},
}

pytestmark = pytest.mark.skipif(not SRC, reason="set ARENA_COMPAT_DB to run")


def _dump(path: Path) -> dict[str, tuple[list[str], list[tuple]]]:
    db = sqlite3.connect(path)
    out = {}
    for (t,) in db.execute("select name from sqlite_master where type='table' "
                           "and name not like 'sqlite_%'"):
        cols = [r[1] for r in db.execute(f"pragma table_info({t})")]
        out[t] = (cols, db.execute(f"select * from {t} order by rowid").fetchall())
    db.close()
    return out


def test_upgrade_leaves_existing_data_untouched(tmp_path):
    copy = tmp_path / "arena.db"
    shutil.copy(SRC, copy)
    before = _dump(copy)
    backend = Path(__file__).resolve().parent.parent
    env = {**os.environ, "ARENA_DB": str(copy)}
    subprocess.run([sys.executable, "-c", "from app.db import init_db; init_db()"],
                   cwd=backend, env=env, check=True)
    after = _dump(copy)

    assert "oraclereplica" in after and "machineclass" in after
    for table, cols in NEW_COLUMNS.items():
        assert cols <= set(after[table][0]), table
    assert len(after["run"][1]) == len(before["run"][1]) > 0

    for table, (cols, rows) in before.items():
        new_cols, new_rows = after[table]
        assert new_cols[:len(cols)] == cols, table      # columns only appended
        assert len(new_rows) == len(rows), table
        assert [r[:len(cols)] for r in new_rows] == rows, table
        added = set(new_cols[len(cols):])
        assert added <= NEW_COLUMNS.get(table, set()), (table, added)
        for i, c in enumerate(new_cols):
            if c in added:
                assert all(r[i] is None for r in new_rows), (table, c)

    sys.path.insert(0, str(backend))
    from app.measurement_profile import config_hash
    import json
    db = sqlite3.connect(copy)
    for tool, cfg, h in db.execute(
            "select energy_tool, config, config_hash from measurementprofile"):
        assert config_hash(tool, json.loads(cfg) if cfg else None) == h
    oracles = db.execute(
        "select 'h', id from harness where kind in ('arena-local','custom') "
        "and json_extract(config, '$.endpoint') is not null "
        "union all select 'o', id from correctnessoracle where kind in "
        "('arena-local','custom') and json_extract(config, '$.endpoint') is not null"
    ).fetchall()
    for kind, oid in oracles:
        col = "harness_id" if kind == "h" else "correctness_oracle_id"
        n = db.execute(f"select count(*) from oraclereplica where {col}=? "
                       f"and is_primary=1", (oid,)).fetchone()[0]
        assert n == 1, (kind, oid)
    db.close()
