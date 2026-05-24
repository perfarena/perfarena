"""Seed the DB.

- Problems from `data/sampled_problems.json` if present (the 99 problems chosen
  by `python -m app.sample`); otherwise fall back to every row in
  `data/problems.parquet`.
- A bootstrap admin from env vars ARENA_ADMIN_EMAIL / ARENA_ADMIN_NAME.

`--reset` wipes every Problem (and its dependent Attempt / Run / Submission /
Baseline rows) before inserting — required when re-sampling, otherwise stale
problems from a previous seed remain in the system.

Run as:
    python -m app.sample              # writes data/sampled_problems.json
    python -m app.seed --reset        # wipes + seeds the 99
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlmodel import Session, select

from .auth import generate_api_key
from .db import engine, init_db
from .models import Problem, User, UserRole, UserStatus


DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"
SAMPLED_JSON = DATA_DIR / "sampled_problems.json"
PROBLEMS_PARQUET = DATA_DIR / "problems.parquet"


def _wipe_problems_and_dependents(session: Session) -> None:
    """SQLite has no ON DELETE CASCADE on our FKs — clear dependents explicitly,
    in FK order. Anything that referenced a Problem id is removed."""
    for table in ("tracelogentry", "baseline", "run", "attempt",
                  "submission", "problem"):
        session.exec(text(f"DELETE FROM {table}"))
    session.commit()


def _load_sampled_rows() -> list[dict] | None:
    if not SAMPLED_JSON.exists():
        return None
    return json.loads(SAMPLED_JSON.read_text())


def _load_parquet_rows() -> list[dict]:
    if not PROBLEMS_PARQUET.exists():
        return []
    df = pd.read_parquet(PROBLEMS_PARQUET).drop_duplicates("title_slug", keep="first")
    rows: list[dict] = []
    for _, r in df.iterrows():
        rows.append({
            "external_id": int(r["id"]),
            "title_slug": r["title_slug"],
            "title": r["title_slug"].replace("-", " ").title(),
            "level": r["level"],
            "tags": r["tag"] or "",
            "content": r["question_content"] or "",
            "hints": None if pd.isna(r.get("question_hints")) else r.get("question_hints"),
        })
    return rows


def seed_problems(session: Session, *, reset: bool) -> int:
    rows = _load_sampled_rows()
    source = SAMPLED_JSON
    if rows is None:
        rows = _load_parquet_rows()
        source = PROBLEMS_PARQUET
    if not rows:
        print(f"skip problems: no source data ({source} not found)")
        return 0
    if reset:
        _wipe_problems_and_dependents(session)
    inserted = 0
    for r in rows:
        if session.exec(
            select(Problem).where(Problem.title_slug == r["title_slug"])
        ).first():
            continue
        session.add(Problem(**r))
        inserted += 1
    session.commit()
    print(f"problems source: {source.name}  inserted: {inserted}  rows: {len(rows)}")
    return inserted


def seed_admin(session: Session) -> str | None:
    email = os.environ.get("ARENA_ADMIN_EMAIL")
    name = os.environ.get("ARENA_ADMIN_NAME", "Arena Admin")
    if not email:
        return None
    existing = session.exec(select(User).where(User.email == email)).first()
    if existing:
        return existing.api_key
    key = generate_api_key()
    user = User(
        email=email, display_name=name, role=UserRole.admin,
        status=UserStatus.approved, api_key=key,
    )
    session.add(user)
    session.commit()
    return key


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true",
                    help="WIPE every Problem and its dependent rows before seeding "
                         "(required when re-sampling to remove stale problems).")
    args = ap.parse_args()

    init_db()
    with Session(engine) as session:
        seed_problems(session, reset=args.reset)
        key = seed_admin(session)
        if key:
            print(f"admin api key: {key}")


if __name__ == "__main__":
    main()
