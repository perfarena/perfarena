"""Admin export / import: snapshot the platform's content into a JSON or
CSV bundle, and reverse the operation on another instance to merge replicas.

Replicas run independently (different LeetCode accounts, parallel sweeps)
and converge here. The dedup contract per table:

  * **Natural-key tables** — User (email), Problem (title_slug),
    PromptTemplate (name), RegisteredModel (name+version),
    Competition (slug), BlogPost (slug), Baseline (problem+language).
    Rows whose natural key already exists are SKIPPED. The local row stays
    untouched (admin curation isn't blown away by an import).

  * **Surrogate-key tables** — Submission, Attempt, Run, CompetitionProblem.
    The primary key IS the foreign key target for child rows, so we can't
    blindly preserve incoming ids without collisions. Instead we INSERT
    with new local ids and remap every FK on the fly (old_id → new_id).

Audit-chain tables (TraceLogEntry, Snapshot, BaselineRefreshState) are
intentionally excluded — the hash chain is per-instance by construction
and merging it across replicas would break verification. Document only.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable

from sqlmodel import Session, select

from .models import (
    Attempt, Baseline, BlogPost, Competition, CompetitionProblem,
    Problem, PromptTemplate, RegisteredModel, Run, Submission, User,
)


# ---------- which tables can be exported / imported -------------------------
#
# `name` is the wire format key. `model` is the SQLModel class. Order matters
# for IMPORT: parents must be inserted before children so FK remaps resolve.

EXPORTABLE: list[tuple[str, type]] = [
    ("users",                User),
    ("problems",             Problem),
    ("prompt_templates",     PromptTemplate),
    ("registered_models",    RegisteredModel),
    ("competitions",         Competition),
    ("competition_problems", CompetitionProblem),
    ("baselines",            Baseline),
    ("blog_posts",           BlogPost),
    ("submissions",          Submission),
    ("attempts",             Attempt),
    ("runs",                 Run),
]
TABLE_BY_NAME = {name: model for name, model in EXPORTABLE}


# ---------- serialization helpers -------------------------------------------

def _row_to_dict(row) -> dict:
    """SQLModel row → wire dict. Strips python-specific objects (datetimes
    become ISO strings, JSON columns stay nested objects) and keeps the
    schema flat for both JSON and CSV formats."""
    out: dict[str, Any] = {}
    for col in row.__class__.__table__.columns.keys():           # type: ignore[attr-defined]
        v = getattr(row, col, None)
        if isinstance(v, datetime):
            out[col] = v.isoformat()
        elif hasattr(v, "value") and not isinstance(v, (str, int, float, bool, dict, list)):
            # SQLModel Enum columns store the enum instance — unwrap.
            out[col] = v.value
        else:
            out[col] = v
    return out


def _coerce_for_table(model: type, row: dict) -> dict:
    """Trim columns to those the table actually defines (lets a future
    schema-add not break older imports) + parse ISO timestamps back."""
    valid = set(model.__table__.columns.keys())                  # type: ignore[attr-defined]
    out: dict[str, Any] = {}
    for k, v in row.items():
        if k not in valid:
            continue
        col = model.__table__.columns[k]                         # type: ignore[attr-defined]
        # Best-effort datetime parsing; SQLite stores them as text anyway,
        # but the SQLModel layer wants real datetimes for default columns.
        if v is not None and str(col.type).upper().startswith("DATETIME"):
            try:
                out[k] = datetime.fromisoformat(str(v))
                continue
            except ValueError:
                pass
        out[k] = v
    return out


# ---------- export ----------------------------------------------------------

def export_json(session: Session, tables: list[str]) -> dict:
    """Build the canonical export envelope. Tables that aren't recognised
    are dropped silently (the endpoint validates input separately)."""
    selected = [(name, m) for name, m in EXPORTABLE if name in tables]
    data: dict[str, list[dict]] = {}
    counts: dict[str, int] = {}
    for name, model in selected:
        rows = [_row_to_dict(r) for r in session.exec(select(model)).all()]
        data[name] = rows
        counts[name] = len(rows)
    return {
        "version": 1,
        "exported_at": datetime.utcnow().isoformat(),
        "tables": [name for name, _ in selected],
        "counts": counts,
        "data": data,
    }


def export_csv_zip(session: Session, tables: list[str]) -> bytes:
    """Zip with one CSV per table — handy for spreadsheet inspection.
    JSON columns are serialised inline as their JSON string so the cell
    is a string a human can read; round-trip via CSV import isn't
    supported (use JSON for that)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, model in EXPORTABLE:
            if name not in tables:
                continue
            rows = [_row_to_dict(r) for r in session.exec(select(model)).all()]
            cols = list(model.__table__.columns.keys())          # type: ignore[attr-defined]
            sio = io.StringIO()
            writer = csv.DictWriter(sio, fieldnames=cols)
            writer.writeheader()
            for r in rows:
                writer.writerow({
                    c: (json.dumps(r.get(c), sort_keys=True)
                        if isinstance(r.get(c), (dict, list))
                        else r.get(c))
                    for c in cols
                })
            zf.writestr(f"{name}.csv", sio.getvalue())
        # Drop a manifest so the recipient knows what's in the zip.
        zf.writestr("manifest.json", json.dumps({
            "version": 1,
            "exported_at": datetime.utcnow().isoformat(),
            "tables": [n for n, _ in EXPORTABLE if n in tables],
        }, indent=2))
    return buf.getvalue()


# ---------- import / merge --------------------------------------------------

# (table_name, natural-key extractor → tuple). When a row's natural key
# already exists locally we SKIP — admin-curated content takes precedence
# over an incoming replica's version.
_NATURAL_KEYS: dict[str, Callable[[dict], tuple]] = {
    "users":              lambda r: (r.get("email"),),
    "problems":           lambda r: (r.get("title_slug"),),
    "prompt_templates":   lambda r: (r.get("name"),),
    "registered_models":  lambda r: (r.get("name"), r.get("version")),
    "competitions":       lambda r: (r.get("slug"),),
    "blog_posts":         lambda r: (r.get("slug"),),
    "baselines":          lambda r: (r.get("problem_id"), r.get("language")),
    # composite-PK join — natural key IS the PK.
    "competition_problems": lambda r: (r.get("competition_id"), r.get("problem_id")),
}


def _existing_natural_keys(session: Session, name: str) -> set[tuple]:
    """Snapshot the local instance's natural keys for the given table so we
    can do the merge in O(rows) without a per-row query."""
    model = TABLE_BY_NAME[name]
    keyfn = _NATURAL_KEYS[name]
    return {keyfn(_row_to_dict(r)) for r in session.exec(select(model)).all()}


def import_bundle(session: Session, bundle: dict) -> dict:
    """Merge an export bundle into the live DB. Returns a per-table count of
    inserted / skipped / errored rows.

    Merge semantics:
      * Natural-key tables: skip when key already exists locally; insert
        otherwise PRESERVING the incoming primary key when it's free,
        remapping when it would collide.
      * Surrogate-key tables (Submission, Attempt, Run): always insert with
        a new local id; map old→new ids and rewrite child FKs in flight.
      * Composite-PK join (CompetitionProblem): treat the (comp, problem)
        pair as the natural key; values rewritten via the parent maps.
    """
    if not isinstance(bundle, dict) or "data" not in bundle:
        raise ValueError("import bundle missing `data`")
    incoming = bundle["data"]
    if not isinstance(incoming, dict):
        raise ValueError("`data` must be an object keyed by table name")

    report: dict[str, dict[str, int]] = defaultdict(lambda: {
        "inserted": 0, "skipped": 0, "errored": 0,
    })
    # old_id → new_id per surrogate-key table, populated as we insert.
    id_maps: dict[str, dict[int, int]] = {
        "users": {}, "problems": {}, "prompt_templates": {},
        "registered_models": {}, "competitions": {},
        "blog_posts": {}, "submissions": {}, "attempts": {},
        "runs": {}, "baselines": {},
    }

    # 1) Natural-key tables — insert in declared order so child FKs resolve.
    natural_first = ["users", "problems", "prompt_templates",
                     "registered_models", "competitions", "blog_posts",
                     "baselines"]
    for tname in natural_first:
        rows = incoming.get(tname) or []
        if not rows:
            continue
        model = TABLE_BY_NAME[tname]
        keyfn = _NATURAL_KEYS[tname]
        existing_keys = _existing_natural_keys(session, tname)
        # Existing rows' (natural_key → local id) so we can populate id_maps
        # even when we SKIP.
        existing_ids: dict[tuple, int] = {}
        for r in session.exec(select(model)).all():
            existing_ids[keyfn(_row_to_dict(r))] = getattr(r, "id", None)

        for row in rows:
            try:
                key = keyfn(row)
                if key in existing_keys:
                    # Map the incoming id to the LOCAL id so child FKs land.
                    old_id = row.get("id")
                    if old_id is not None and existing_ids.get(key) is not None:
                        id_maps[tname][int(old_id)] = int(existing_ids[key])
                    report[tname]["skipped"] += 1
                    continue
                payload = _coerce_for_table(model, row)
                # Drop the incoming id — let SQLite assign a fresh one so
                # local ids stay collision-free across replicas.
                old_id = payload.pop("id", None)
                obj = model(**payload)
                session.add(obj)
                session.flush()                              # populate obj.id
                if old_id is not None and getattr(obj, "id", None) is not None:
                    id_maps[tname][int(old_id)] = int(obj.id)
                existing_keys.add(key)
                report[tname]["inserted"] += 1
            except Exception:                                # noqa: BLE001
                report[tname]["errored"] += 1
        session.commit()

    # 2) Submissions — children of users + prompts. Remap FKs.
    for row in incoming.get("submissions") or []:
        try:
            payload = _coerce_for_table(Submission, row)
            old_id = payload.pop("id", None)
            uid = payload.get("user_id")
            if uid is not None:
                mapped = id_maps["users"].get(int(uid))
                if mapped is None:
                    # The incoming user was filtered out / not in bundle:
                    # we can't dangle the FK, so skip the submission.
                    report["submissions"]["errored"] += 1
                    continue
                payload["user_id"] = mapped
            pid = payload.get("prompt_id")
            if pid is not None:
                mapped = id_maps["prompt_templates"].get(int(pid))
                if mapped is not None:
                    payload["prompt_id"] = mapped
                # If the prompt didn't come over we keep the original id —
                # FastAPI will surface a stale-FK only on read paths that
                # actually dereference it.
            obj = Submission(**payload)
            session.add(obj)
            session.flush()
            if old_id is not None:
                id_maps["submissions"][int(old_id)] = int(obj.id)
            report["submissions"]["inserted"] += 1
        except Exception:                                    # noqa: BLE001
            report["submissions"]["errored"] += 1
    session.commit()

    # 3) Attempts — children of submissions + problems.
    for row in incoming.get("attempts") or []:
        try:
            payload = _coerce_for_table(Attempt, row)
            old_id = payload.pop("id", None)
            sid = payload.get("submission_id")
            new_sid = id_maps["submissions"].get(int(sid)) if sid is not None else None
            if new_sid is None:
                report["attempts"]["errored"] += 1
                continue
            payload["submission_id"] = new_sid
            pid = payload.get("problem_id")
            if pid is not None:
                mapped = id_maps["problems"].get(int(pid))
                if mapped is not None:
                    payload["problem_id"] = mapped
            # The chain link previous_attempt_id needs the same remap.
            prev = payload.get("previous_attempt_id")
            if prev is not None:
                payload["previous_attempt_id"] = id_maps["attempts"].get(int(prev), prev)
            obj = Attempt(**payload)
            session.add(obj)
            session.flush()
            if old_id is not None:
                id_maps["attempts"][int(old_id)] = int(obj.id)
            report["attempts"]["inserted"] += 1
        except Exception:                                    # noqa: BLE001
            report["attempts"]["errored"] += 1
    session.commit()

    # 4) Runs — children of attempts.
    for row in incoming.get("runs") or []:
        try:
            payload = _coerce_for_table(Run, row)
            payload.pop("id", None)
            aid = payload.get("attempt_id")
            new_aid = id_maps["attempts"].get(int(aid)) if aid is not None else None
            if new_aid is None:
                report["runs"]["errored"] += 1
                continue
            payload["attempt_id"] = new_aid
            obj = Run(**payload)
            session.add(obj)
            report["runs"]["inserted"] += 1
        except Exception:                                    # noqa: BLE001
            report["runs"]["errored"] += 1
    session.commit()

    # 5) Competition membership — composite join, FKs remap via parents.
    for row in incoming.get("competition_problems") or []:
        try:
            cid = row.get("competition_id")
            pid = row.get("problem_id")
            new_cid = id_maps["competitions"].get(int(cid)) if cid else None
            new_pid = id_maps["problems"].get(int(pid)) if pid else None
            # If the parents weren't in the bundle but exist locally with
            # the SAME id, the remap is a no-op — keep the incoming pair.
            new_cid = new_cid or cid
            new_pid = new_pid or pid
            if new_cid is None or new_pid is None:
                report["competition_problems"]["errored"] += 1
                continue
            existing = session.exec(
                select(CompetitionProblem)
                .where(CompetitionProblem.competition_id == int(new_cid),
                       CompetitionProblem.problem_id == int(new_pid))
            ).first()
            if existing is not None:
                report["competition_problems"]["skipped"] += 1
                continue
            session.add(CompetitionProblem(
                competition_id=int(new_cid), problem_id=int(new_pid),
            ))
            report["competition_problems"]["inserted"] += 1
        except Exception:                                    # noqa: BLE001
            report["competition_problems"]["errored"] += 1
    session.commit()

    return {"counts": dict(report), "bundle_version": bundle.get("version", 1)}
