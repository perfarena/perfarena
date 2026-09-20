"""Admin export / import: snapshot the platform's content into a JSON or
CSV bundle, and reverse the operation on another instance to merge replicas.

Replicas run independently (different LeetCode accounts, parallel sweeps)
and converge here. The dedup contract per table:

  * **Natural-key tables** — User (email), Problem (title_slug),
    PromptTemplate (name), RegisteredModel (name+version),
    Competition (slug), Harness (slug), BlogPost (slug),
    Baseline (problem+language). Rows whose natural key already exists
    are SKIPPED. The local row stays untouched (admin curation isn't
    blown away by an import).

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
    CorrectnessOracle, Harness, Machine, MeasurementProfile, Problem,
    PromptTemplate, RegisteredModel, Run, Submission, User,
)
from . import leetcode_accounts as _lc_accounts


# ---------- which tables can be exported / imported -------------------------
#
# `name` is the wire format key. `model` is the SQLModel class. Order matters
# for IMPORT: parents must be inserted before children so FK remaps resolve.

EXPORTABLE: list[tuple[str, type]] = [
    ("users",                User),
    ("problems",             Problem),
    ("prompt_templates",     PromptTemplate),
    ("registered_models",    RegisteredModel),
    # Harnesses (perf oracle) come BEFORE competitions/submissions/runs
    # because those all carry a harness_id FK that has to remap through
    # id_maps below. Correctness oracles sit right next to them for the
    # symmetric reason — Competition/Submission/Run FK into both.
    ("harnesses",            Harness),
    ("correctness_oracles",  CorrectnessOracle),
    # Provenance of a run's numbers: the physical box + the (tool, config)
    # profile it ran under. Machine before profiles (profile FKs machine);
    # both before submissions/runs, which FK into them.
    ("machines",             Machine),
    ("measurement_profiles", MeasurementProfile),
    ("competitions",         Competition),
    ("competition_problems", CompetitionProblem),
    ("baselines",            Baseline),
    ("blog_posts",           BlogPost),
    ("submissions",          Submission),
    ("attempts",             Attempt),
    ("runs",                 Run),
]
TABLE_BY_NAME = {name: model for name, model in EXPORTABLE}

# LeetCode accounts are file-backed (data/leetcode_accounts.json) rather
# than a SQLModel, so they're listed separately and handled with a special-
# case branch in export_json / import_bundle. Wire name lives next to the
# DB tables so a UI can offer it as just another checkbox.
LC_ACCOUNTS_NAME = "leetcode_accounts"
# Public name that admin_export_tables surfaces — DB tables first, then the
# file-backed leetcode_accounts slot. Order kept stable so the SPA can
# preserve its rendered list without per-version changes.
ALL_EXPORT_NAMES = [name for name, _ in EXPORTABLE] + [LC_ACCOUNTS_NAME]


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

def _redact_cookie(cookie_header: str) -> str:
    """Mask the LEETCODE_SESSION JWT and csrftoken in a cookie header,
    keeping name=keyhint pairs for human inspection. Lets an operator
    audit "is account X still in the bundle?" without leaking the
    actual session secret."""
    if not cookie_header:
        return cookie_header
    out_pieces: list[str] = []
    for piece in cookie_header.split(";"):
        piece = piece.strip()
        if "=" in piece:
            k, _, v = piece.partition("=")
            kl = k.strip().lower()
            if kl in ("leetcode_session", "csrftoken") and v:
                # Keep just enough characters to identify the account
                # while making the cookie unusable. 4 chars × 2 → 8.
                head = v.strip()[:4]
                out_pieces.append(f"{k.strip()}={head}…<redacted>")
            else:
                out_pieces.append(piece)
        else:
            out_pieces.append(piece)
    return "; ".join(out_pieces)


# Secret columns on the User table. api_key is stored in plaintext at rest, so
# an unredacted export would hand out every user's live key; password_hash is a
# bcrypt digest but still secret. Both are masked unless credentials are
# explicitly requested. The marker is recognised on import so a round-trip
# never overwrites a real key with the mask.
_USER_SECRET_COLS = ("api_key", "password_hash")
_REDACTED = "[redacted]"


def _redact_user_rows(rows: list[dict], *, include_credentials: bool) -> list[dict]:
    if include_credentials:
        return rows
    masked = []
    for r in rows:
        r = dict(r)
        for col in _USER_SECRET_COLS:
            if r.get(col):
                r[col] = _REDACTED
        masked.append(r)
    return masked


def _serialize_leetcode_accounts(*, include_credentials: bool) -> list[dict]:
    """Read the file-backed account pool and return JSON-safe dicts.

    With `include_credentials=False` (default) every cookie value is
    redacted to <head>…<redacted> so the bundle can be inspected without
    leaking session JWTs. With `include_credentials=True` the raw
    `cookie_header` is shipped — the caller is responsible for treating
    the bundle as secret (encrypt at rest, do not commit to git, etc.)."""
    accounts = _lc_accounts.load_accounts()
    out: list[dict] = []
    for a in accounts:
        out.append({
            "name": a.name,
            "cookie_header": (
                a.cookie_header if include_credentials
                else _redact_cookie(a.cookie_header)
            ),
            "exhausted_until": a.exhausted_until,
            "last_used_at": a.last_used_at,
            # Audit hint for operators reading a redacted bundle. Never
            # surfaces the secret itself, just whether the row is usable.
            "has_cookie": bool(a.cookie_header),
        })
    return out


def export_json(session: Session, tables: list[str],
                *, include_credentials: bool = False) -> dict:
    """Build the canonical export envelope. Tables that aren't recognised
    are dropped silently (the endpoint validates input separately).

    `include_credentials` only affects the leetcode_accounts slot — when
    False (default), session cookies are redacted. The flag is a no-op
    for DB tables (none of them store credentials)."""
    selected = [(name, m) for name, m in EXPORTABLE if name in tables]
    data: dict[str, list[dict]] = {}
    counts: dict[str, int] = {}
    for name, model in selected:
        rows = [_row_to_dict(r) for r in session.exec(select(model)).all()]
        if name == "users":
            rows = _redact_user_rows(rows, include_credentials=include_credentials)
        data[name] = rows
        counts[name] = len(rows)
    # File-backed leetcode_accounts — listed in `tables` like any other
    # checkbox the SPA renders. Special-cased here because the source
    # is a JSON file rather than a SQLModel.
    if LC_ACCOUNTS_NAME in tables:
        rows = _serialize_leetcode_accounts(
            include_credentials=include_credentials,
        )
        data[LC_ACCOUNTS_NAME] = rows
        counts[LC_ACCOUNTS_NAME] = len(rows)
    envelope = {
        "version": 1,
        "exported_at": datetime.utcnow().isoformat(),
        "tables": [name for name, _ in selected] + (
            [LC_ACCOUNTS_NAME] if LC_ACCOUNTS_NAME in tables else []
        ),
        "counts": counts,
        "data": data,
    }
    if LC_ACCOUNTS_NAME in tables:
        envelope["leetcode_accounts_credentials_included"] = bool(
            include_credentials
        )
    return envelope


def export_csv_zip(session: Session, tables: list[str],
                   *, include_credentials: bool = False) -> bytes:
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
            if name == "users":
                rows = _redact_user_rows(rows, include_credentials=include_credentials)
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
        if LC_ACCOUNTS_NAME in tables:
            rows = _serialize_leetcode_accounts(
                include_credentials=include_credentials,
            )
            cols = ["name", "cookie_header", "exhausted_until",
                    "last_used_at", "has_cookie"]
            sio = io.StringIO()
            writer = csv.DictWriter(sio, fieldnames=cols)
            writer.writeheader()
            for r in rows:
                writer.writerow({c: r.get(c) for c in cols})
            zf.writestr(f"{LC_ACCOUNTS_NAME}.csv", sio.getvalue())
        # Drop a manifest so the recipient knows what's in the zip.
        manifest = {
            "version": 1,
            "exported_at": datetime.utcnow().isoformat(),
            "tables": (
                [n for n, _ in EXPORTABLE if n in tables]
                + ([LC_ACCOUNTS_NAME] if LC_ACCOUNTS_NAME in tables else [])
            ),
        }
        if LC_ACCOUNTS_NAME in tables:
            manifest["leetcode_accounts_credentials_included"] = bool(
                include_credentials
            )
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
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
    "harnesses":          lambda r: (r.get("slug"),),
    "correctness_oracles": lambda r: (r.get("slug"),),
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
        "registered_models": {}, "competitions": {}, "harnesses": {},
        "correctness_oracles": {}, "machines": {}, "measurement_profiles": {},
        "blog_posts": {}, "submissions": {}, "attempts": {},
        "runs": {}, "baselines": {},
    }

    # 1) Natural-key tables — insert in declared order so child FKs resolve.
    # `harnesses` and `correctness_oracles` before `competitions` because
    # Competition FKs into both and we want the remaps ready.
    natural_first = ["users", "problems", "prompt_templates",
                     "registered_models", "harnesses",
                     "correctness_oracles", "competitions",
                     "blog_posts", "baselines"]
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
                # A redacted export carries the "[redacted]" marker for user
                # secrets. Never persist the marker as a real credential — drop
                # it so an imported user simply has no key until one is issued.
                if tname == "users":
                    for _c in _USER_SECRET_COLS:
                        if payload.get(_c) == _REDACTED:
                            payload[_c] = None
                # Drop the incoming id — let SQLite assign a fresh one so
                # local ids stay collision-free across replicas.
                old_id = payload.pop("id", None)
                # FK remaps for natural-key tables. Competition FKs into
                # BOTH oracles (performance + correctness); translate each
                # through its own id_maps entry so bindings land at the
                # local ids on the target instance. NULLs pass through.
                if tname == "competitions":
                    hid = payload.get("harness_id")
                    if hid is not None:
                        payload["harness_id"] = id_maps["harnesses"].get(int(hid), hid)
                    coid = payload.get("correctness_oracle_id")
                    if coid is not None:
                        payload["correctness_oracle_id"] = (
                            id_maps["correctness_oracles"].get(int(coid), coid)
                        )
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

    # 1b) Machines + measurement profiles — a run's measurement provenance.
    # Machine has no FKs; MeasurementProfile.machine_id remaps through it.
    for row in incoming.get("machines") or []:
        try:
            payload = _coerce_for_table(Machine, row)
            old_id = payload.pop("id", None)
            obj = Machine(**payload)
            session.add(obj)
            session.flush()
            if old_id is not None:
                id_maps["machines"][int(old_id)] = int(obj.id)
            report["machines"]["inserted"] += 1
        except Exception:                                    # noqa: BLE001
            report["machines"]["errored"] += 1
    session.commit()

    for row in incoming.get("measurement_profiles") or []:
        try:
            payload = _coerce_for_table(MeasurementProfile, row)
            old_id = payload.pop("id", None)
            mid = payload.get("machine_id")
            if mid is not None:
                payload["machine_id"] = id_maps["machines"].get(int(mid), mid)
            obj = MeasurementProfile(**payload)
            session.add(obj)
            session.flush()
            if old_id is not None:
                id_maps["measurement_profiles"][int(old_id)] = int(obj.id)
            report["measurement_profiles"]["inserted"] += 1
        except Exception:                                    # noqa: BLE001
            report["measurement_profiles"]["errored"] += 1
    session.commit()

    # 2) Submissions — children of users + prompts + competitions + harnesses.
    # Remap all four FKs; drop the submission when its user_id can't be
    # resolved (unresolvable user = orphan row).
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
            cid = payload.get("competition_id")
            if cid is not None:
                payload["competition_id"] = id_maps["competitions"].get(int(cid), cid)
            hid = payload.get("harness_id")
            if hid is not None:
                payload["harness_id"] = id_maps["harnesses"].get(int(hid), hid)
            coid = payload.get("correctness_oracle_id")
            if coid is not None:
                payload["correctness_oracle_id"] = (
                    id_maps["correctness_oracles"].get(int(coid), coid)
                )
            mid = payload.get("machine_id")
            if mid is not None:
                payload["machine_id"] = id_maps["machines"].get(int(mid), mid)
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

    # 4) Runs — children of attempts. Also carries a harness_id FK that
    # records which oracle actually judged the run; remap through the
    # harnesses id_maps so the local run points at the local harness row.
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
            hid = payload.get("harness_id")
            if hid is not None:
                payload["harness_id"] = id_maps["harnesses"].get(int(hid), hid)
            coid = payload.get("correctness_oracle_id")
            if coid is not None:
                payload["correctness_oracle_id"] = (
                    id_maps["correctness_oracles"].get(int(coid), coid)
                )
            mpid = payload.get("measurement_profile_id")
            if mpid is not None:
                payload["measurement_profile_id"] = (
                    id_maps["measurement_profiles"].get(int(mpid), mpid)
                )
            obj = Run(**payload)
            session.add(obj)
            report["runs"]["inserted"] += 1
        except Exception:                                    # noqa: BLE001
            report["runs"]["errored"] += 1
    session.commit()

    # 4b) LeetCode accounts — file-backed, merge by name.
    #
    # Semantics:
    #   * If the incoming `cookie_header` looks REDACTED (no LEETCODE_SESSION,
    #     or it ends in "<redacted>"), the row is skipped silently — useful
    #     when an operator merges a sanitized bundle to pick up account
    #     metadata (exhausted_until, name list) without touching local
    #     creds.
    #   * If a usable cookie_header is present AND the name doesn't exist
    #     locally yet → ADD it.
    #   * If the name already exists locally → SKIP (local cookies win,
    #     same admin-curation rule as the natural-key DB tables). Operator
    #     can `--remove` the local row first if they want to overwrite.
    incoming_accounts = incoming.get(LC_ACCOUNTS_NAME) or []
    if incoming_accounts:
        existing_local = {a.name for a in _lc_accounts.load_accounts()}
        for row in incoming_accounts:
            try:
                name = (row or {}).get("name")
                cookie = (row or {}).get("cookie_header") or ""
                if not name:
                    report[LC_ACCOUNTS_NAME]["errored"] += 1
                    continue
                cookie_low = cookie.lower()
                looks_redacted = (
                    "<redacted>" in cookie_low
                    or "leetcode_session" not in cookie_low
                    or "csrftoken" not in cookie_low
                )
                if looks_redacted or not cookie:
                    report[LC_ACCOUNTS_NAME]["skipped"] += 1
                    continue
                if name in existing_local:
                    # Same precedence rule as natural-key DB tables.
                    report[LC_ACCOUNTS_NAME]["skipped"] += 1
                    continue
                _lc_accounts.add_account(name, cookie)
                existing_local.add(name)
                report[LC_ACCOUNTS_NAME]["inserted"] += 1
            except Exception:                                    # noqa: BLE001
                report[LC_ACCOUNTS_NAME]["errored"] += 1

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
