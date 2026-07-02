#!/usr/bin/env python3
"""Recompute attempt.cost_usd from stored trace.provider_meta.usage.

Why this exists: the OpenAI pricing table in submit_with_local_model.py
was missing `gpt-5.5` (every attempt billed as $0.00) and was billing
cached input tokens at the full input rate. Past attempts were ingested
before either fix landed, so their `cost_usd` column is wrong but the
raw usage data is still on the row inside `attempt.trace`.

This script re-prices every eligible attempt locally:

  cost = (fresh × in_rate) + (cached × cached_rate) + (out × out_rate) / 1M

It's idempotent and dry-run by default. Pass `--apply` to commit. Pass
`--model gpt-5.5` to scope to a single model name (handy when iterating
on pricing for one SKU).

Run it from the repo root:

  python webapp/examples/backfill_costs.py                # dry-run, show diff
  python webapp/examples/backfill_costs.py --apply        # commit
  python webapp/examples/backfill_costs.py --model gpt-5.5 --apply

What it does NOT touch:
  * Submission.total_cost_usd — recomputed lazily by main.py per request,
    and immediately on the next /recompute_submission_costs call (which
    fires whenever an attempt is created or a model price is edited). If
    you want it eagerly fixed, hit the admin "recompute costs" endpoint
    after this script.
  * attempt.cost_usd for rows where trace.provider != "openai" (Ollama /
    canned-fallback are correctly 0).
  * Rows whose trace is missing `provider_meta.usage` (we can't re-price
    what wasn't reported in the first place).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Add repo paths so we can import the live pricing table + cost fn from
# the submit script (single source of truth for the model→rate map) and
# SQLModel session helpers from the backend app.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "webapp" / "examples"))
sys.path.insert(0, str(ROOT / "webapp" / "backend"))

from submit_with_local_model import (  # noqa: E402
    OPENAI_PRICING_USD_PER_1M,
    _openai_cost_from_trace,
)
from sqlmodel import Session, select  # noqa: E402
from app.db import engine, init_db  # noqa: E402
from app.models import Attempt  # noqa: E402

# init_db runs the same additive migrations the FastAPI app runs on
# startup. Without this, opening a fresh engine here trips
# "no such column" on the columns the live SPA already uses (prompt_tokens
# was the most recent). Idempotent — running it is a no-op when columns
# already exist.
init_db()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="Commit changes. Default is dry-run.")
    parser.add_argument("--model", default=None,
                        help="Restrict to attempts whose trace.model matches "
                             "this prefix (e.g. 'gpt-5.5').")
    parser.add_argument("--only-zero", action="store_true",
                        help="Only re-price attempts currently stored as $0.00. "
                             "Use when you want to fix the missing-from-table "
                             "case without touching cache-discount over-billing.")
    args = parser.parse_args()

    db_url = os.environ.get("ARENA_DATABASE_URL") or "(default sqlite)"
    print(f"[backfill] db = {db_url}")
    print(f"[backfill] mode = {'APPLY' if args.apply else 'dry-run'}")
    if args.model:
        print(f"[backfill] model filter = {args.model}*")
    if args.only_zero:
        print(f"[backfill] only-zero = True (skipping non-zero rows)")

    n_scanned = 0
    n_skipped_no_trace = 0
    n_skipped_no_usage = 0
    n_skipped_unknown_model = 0
    n_skipped_unchanged = 0
    n_updated = 0
    total_old = 0.0
    total_new = 0.0

    with Session(engine) as session:
        # Stream by id — the JSON column on every Attempt is a few KB so
        # we don't want to materialise the full table in memory. Order by
        # id so progress logs stay monotonic.
        for a in session.exec(
            select(Attempt).order_by(Attempt.id)
        ):
            n_scanned += 1
            trace = a.trace
            if not trace:
                n_skipped_no_trace += 1
                continue
            if trace.get("provider") != "openai":
                # Ollama / canned-fallback / unknown — already $0 and stays $0.
                continue
            model = (trace.get("model") or "").strip()
            if args.model and not model.startswith(args.model):
                continue
            usage = (trace.get("provider_meta") or {}).get("usage") or {}
            if not usage:
                n_skipped_no_usage += 1
                continue
            # Look up the model in the (now-updated) pricing table. If
            # it's still missing the cost will come back 0.0 — flag it
            # so the operator can decide whether to add a row.
            base = model
            if base not in OPENAI_PRICING_USD_PER_1M:
                # _openai_cost_from_trace handles the YYYY-MM-DD strip;
                # we only need the unknown-model warning here.
                stripped = base.rsplit("-", 3)
                if len(stripped) == 4 and "".join(stripped[1:]).isdigit():
                    pass  # date-pinned alias, _openai_cost_from_trace will resolve
                else:
                    n_skipped_unknown_model += 1
                    if n_skipped_unknown_model <= 5:
                        print(f"  [warn] attempt {a.id}: model '{model}' "
                              f"not in pricing table — leaving cost_usd as is")
                    continue
            new_cost = _openai_cost_from_trace(trace)
            old_cost = float(a.cost_usd or 0.0)
            if args.only_zero and old_cost > 0:
                n_skipped_unchanged += 1
                continue
            # Round to 6 decimals before comparing — float jitter from
            # the trace JSON round-trip shouldn't be treated as a change.
            if round(new_cost, 6) == round(old_cost, 6):
                n_skipped_unchanged += 1
                continue
            delta = new_cost - old_cost
            total_old += old_cost
            total_new += new_cost
            n_updated += 1
            if n_updated <= 10 or n_updated % 100 == 0:
                print(f"  attempt {a.id} ({model}): "
                      f"${old_cost:.6f} → ${new_cost:.6f} "
                      f"({'+' if delta >= 0 else ''}{delta*1000:.3f} mUSD)")
            if args.apply:
                a.cost_usd = new_cost
                session.add(a)
        if args.apply:
            session.commit()

    print()
    print(f"[backfill] scanned          : {n_scanned}")
    print(f"[backfill] no trace         : {n_skipped_no_trace}")
    print(f"[backfill] no usage block   : {n_skipped_no_usage}")
    print(f"[backfill] unknown model    : {n_skipped_unknown_model}")
    print(f"[backfill] unchanged        : {n_skipped_unchanged}")
    print(f"[backfill] would update     : {n_updated}")
    print(f"[backfill] total old        : ${total_old:.4f}")
    print(f"[backfill] total new        : ${total_new:.4f}")
    print(f"[backfill] delta            : "
          f"{'+' if total_new >= total_old else ''}${total_new - total_old:.4f}")
    if not args.apply and n_updated:
        print()
        print("[backfill] Re-run with --apply to commit. Then call the admin "
              "endpoint to refresh Submission.total_cost_usd:")
        print("  curl -X POST http://localhost:8000/api/admin/recompute-costs \\")
        print("       -H 'Authorization: Bearer <ADMIN_KEY>'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
