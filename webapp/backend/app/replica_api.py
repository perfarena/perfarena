"""HTTP surface for replica pools, machine classes, calibration, and the
work queue (see replicas.py and scheduler.py). Reads are public, except
calibration history and the queue view. Writes are admin-only.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlmodel import Session, select

from . import replicas, scheduler
from .auth import current_admin
from .db import get_session
from .models import (
    Attempt, CorrectnessOracle, Harness, Machine, MachineClass, OracleReplica,
    ReplicaCalibration, ReplicaResult, Run, Submission, User,
)
from .schemas import (
    CalibrateIn, MachineClassAssignIn, MachineClassIn, MachineClassPublic,
    ReplicaIn, ReplicaPatch, ReplicaPublic,
)

router = APIRouter()

_STATUSES = {"active", "disabled", "quarantined"}


def _public(r: OracleReplica) -> ReplicaPublic:
    ep = replicas._ep(r)
    with replicas._CV:
        until, kind = replicas._cool.get(ep, (0.0, ""))
        return ReplicaPublic(
            **r.model_dump(exclude={"auth_token"}),
            has_auth_token=bool(r.auth_token),
            load=replicas._load.get(ep, 0),
            cooling=kind if until > time.monotonic() else None,
            latency_s=replicas._ewma_s.get(ep))


def _oracle(session: Session, model, slug: str):
    o = session.exec(select(model).where(model.slug == slug)).first()
    if o is None:
        raise HTTPException(404, f"{model.__name__} {slug!r} not found")
    return o


# ---------- replicas -----------------------------------------------------------


@router.get("/api/harnesses/{slug}/replicas", response_model=list[ReplicaPublic])
def list_harness_replicas(slug: str, session: Session = Depends(get_session)):
    return [_public(r) for r in replicas.pool(session, _oracle(session, Harness, slug))]


@router.get("/api/correctness-oracles/{slug}/replicas",
            response_model=list[ReplicaPublic])
def list_oracle_replicas(slug: str, session: Session = Depends(get_session)):
    o = _oracle(session, CorrectnessOracle, slug)
    return [_public(r) for r in replicas.pool(session, o)]


@router.post("/api/admin/replicas", response_model=ReplicaPublic,
             status_code=status.HTTP_201_CREATED)
def add_replica(payload: ReplicaIn, _: User = Depends(current_admin),
                session: Session = Depends(get_session)):
    if bool(payload.harness_slug) == bool(payload.correctness_oracle_slug):
        raise HTTPException(400, "set exactly one of harness_slug / "
                                 "correctness_oracle_slug")
    if payload.status not in _STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(_STATUSES)}")
    if payload.harness_slug:
        o = _oracle(session, Harness, payload.harness_slug)
    else:
        o = _oracle(session, CorrectnessOracle, payload.correctness_oracle_slug)
    if o.kind not in ("arena-local", "custom"):
        raise HTTPException(400, f"{o.slug!r} (kind={o.kind}) has no runner "
                                 f"endpoints; replicas need arena-local/custom")
    r = OracleReplica(
        **payload.model_dump(exclude={"harness_slug", "correctness_oracle_slug"}),
        harness_id=o.id if isinstance(o, Harness) else None,
        correctness_oracle_id=o.id if isinstance(o, CorrectnessOracle) else None)
    r.endpoint = r.endpoint.rstrip("/")
    session.add(r)
    session.commit()
    session.refresh(r)
    return _public(r)


@router.put("/api/admin/replicas/{rid}", response_model=ReplicaPublic)
def edit_replica(rid: int, payload: ReplicaPatch, _: User = Depends(current_admin),
                 session: Session = Depends(get_session)):
    r = session.get(OracleReplica, rid)
    if r is None:
        raise HTTPException(404, "replica not found")
    changes = payload.model_dump(exclude_unset=True)
    if r.is_primary and "endpoint" in changes:
        raise HTTPException(400, "the primary replica follows the oracle's "
                                 "config.endpoint; edit the oracle instead")
    if changes.get("status") not in (None, *_STATUSES):
        raise HTTPException(400, f"status must be one of {sorted(_STATUSES)}")
    if "endpoint" in changes and changes["endpoint"].rstrip("/") != r.endpoint:
        # Another endpoint may be another box: forget machine + admission.
        changes["endpoint"] = changes["endpoint"].rstrip("/")
        r.machine_id, r.admitted_class_id = None, None
    for k, v in changes.items():
        setattr(r, k, v)
    session.add(r)
    session.commit()
    session.refresh(r)
    return _public(r)


@router.delete("/api/admin/replicas/{rid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_replica(rid: int, _: User = Depends(current_admin),
                   session: Session = Depends(get_session)):
    r = session.get(OracleReplica, rid)
    if r is None:
        raise HTTPException(404, "replica not found")
    if r.is_primary:
        raise HTTPException(400, "the primary replica is the oracle's "
                                 "config.endpoint; disable it instead")
    n = session.exec(select(func.count()).select_from(Run)
                     .where(Run.replica_id == rid)).one()
    if n:
        raise HTTPException(409, f"{n} run(s) name this replica as their "
                                 f"provenance; set status=disabled instead")
    for row in session.exec(select(ReplicaCalibration)
                            .where(ReplicaCalibration.replica_id == rid)).all():
        session.delete(row)
    session.delete(r)
    session.commit()


@router.post("/api/admin/replicas/{rid}/probe")
def probe_replica(rid: int, _: User = Depends(current_admin),
                  session: Session = Depends(get_session)):
    r = session.get(OracleReplica, rid)
    if r is None:
        raise HTTPException(404, "replica not found")
    o = (session.get(Harness, r.harness_id) if r.harness_id
         else session.get(CorrectnessOracle, r.correctness_oracle_id))
    ok, msg = replicas.probe_pool(session, o, [r])
    return {"ok": ok, "message": r.last_health_msg or msg}


@router.post("/api/admin/replicas/{rid}/calibrate",
             status_code=status.HTTP_202_ACCEPTED)
def calibrate_replica(rid: int, payload: CalibrateIn,
                      _: User = Depends(current_admin),
                      session: Session = Depends(get_session)):
    """Start the admission-gate run in the background (a reference CLBG
    measurement takes minutes). Poll .../calibrations for the result."""
    r = session.get(OracleReplica, rid)
    if r is None or r.harness_id is None:
        raise HTTPException(404, "performance replica not found")
    cls_id = None
    if payload.machine_class_slug:
        mc = session.exec(select(MachineClass).where(
            MachineClass.slug == payload.machine_class_slug)).first()
        if mc is None:
            raise HTTPException(404, "machine class not found")
        cls_id = mc.id
    replicas.calibrate_in_background(rid, declare_class_id=cls_id,
                                     set_baseline=payload.set_baseline)
    return {"started": True, "replica_id": rid}


@router.get("/api/admin/replicas/{rid}/calibrations",
            response_model=list[ReplicaCalibration])
def list_calibrations(rid: int, _: User = Depends(current_admin),
                      session: Session = Depends(get_session)):
    return session.exec(select(ReplicaCalibration)
                        .where(ReplicaCalibration.replica_id == rid)
                        .order_by(ReplicaCalibration.id.desc())).all()


@router.get("/api/admin/replicas/inconsistent")
def list_inconsistent(_: User = Depends(current_admin),
                      session: Session = Depends(get_session)):
    """Attempts whose correctness replicas disagreed (broadcast_merge /
    quorum), with every replica's vote, for a human to judge."""
    out = []
    rows = session.exec(select(Run).where(
        Run.status_msg == replicas.INCONSISTENT).order_by(Run.id.desc())).all()
    for run in rows:
        a = session.get(Attempt, run.attempt_id)
        votes = session.exec(select(ReplicaResult).where(
            ReplicaResult.attempt_id == run.attempt_id,
            ReplicaResult.stage == "validate")).all()
        out.append({"attempt_id": run.attempt_id,
                    "submission_id": a.submission_id if a else None,
                    "problem_id": a.problem_id if a else None,
                    "votes": [{"replica_id": v.replica_id, "status_msg": v.status_msg,
                               "total_correct": v.total_correct,
                               "total_testcases": v.total_testcases,
                               "created_at": v.created_at} for v in votes]})
    return {"count": len(out), "attempts": out}


# ---------- machine classes ----------------------------------------------------


def _class_public(session: Session, mc: MachineClass) -> MachineClassPublic:
    mids = session.exec(select(Machine.id).where(
        Machine.machine_class_id == mc.id)).all()
    return MachineClassPublic(**mc.model_dump(), machine_ids=list(mids))


@router.get("/api/machine-classes", response_model=list[MachineClassPublic])
def list_machine_classes(session: Session = Depends(get_session)):
    return [_class_public(session, mc) for mc in
            session.exec(select(MachineClass).order_by(MachineClass.slug)).all()]


@router.post("/api/admin/machine-classes", response_model=MachineClassPublic,
             status_code=status.HTTP_201_CREATED)
def add_machine_class(payload: MachineClassIn, _: User = Depends(current_admin),
                      session: Session = Depends(get_session)):
    if session.exec(select(MachineClass).where(
            MachineClass.slug == payload.slug)).first():
        raise HTTPException(409, f"slug {payload.slug!r} already in use")
    mc = MachineClass(**payload.model_dump())
    session.add(mc)
    session.commit()
    session.refresh(mc)
    return _class_public(session, mc)


@router.put("/api/admin/machine-classes/{cid}", response_model=MachineClassPublic)
def edit_machine_class(cid: int, payload: MachineClassIn,
                       _: User = Depends(current_admin),
                       session: Session = Depends(get_session)):
    """Edit a class. The baseline is not editable here: it comes from a
    calibration run (set_baseline=true re-bases it)."""
    mc = session.get(MachineClass, cid)
    if mc is None:
        raise HTTPException(404, "machine class not found")
    clash = session.exec(select(MachineClass).where(
        MachineClass.slug == payload.slug)).first()
    if clash is not None and clash.id != cid:
        raise HTTPException(409, f"slug {payload.slug!r} already in use")
    for k, v in payload.model_dump().items():
        setattr(mc, k, v)
    session.add(mc)
    session.commit()
    session.refresh(mc)
    return _class_public(session, mc)


@router.put("/api/admin/machines/{mid}/class")
def assign_machine_class(mid: int, payload: MachineClassAssignIn,
                         _: User = Depends(current_admin),
                         session: Session = Depends(get_session)):
    """Declare a machine's class (null clears it). A machine's new runs then
    key their profile on the class. Changing the class revokes the admission
    of every replica on the machine until it is recalibrated."""
    m = session.get(Machine, mid)
    if m is None:
        raise HTTPException(404, "machine not found")
    cls_id = None
    if payload.machine_class_slug:
        mc = session.exec(select(MachineClass).where(
            MachineClass.slug == payload.machine_class_slug)).first()
        if mc is None:
            raise HTTPException(404, "machine class not found")
        cls_id = mc.id
    if m.machine_class_id != cls_id:
        for r in session.exec(select(OracleReplica).where(
                OracleReplica.machine_id == mid)).all():
            r.admitted_class_id = None
            session.add(r)
    m.machine_class_id = cls_id
    session.add(m)
    session.commit()
    return {"machine_id": mid, "machine_class_id": cls_id}


# ---------- work queue ------------------------------------------------------------


@router.get("/api/admin/queue")
def queue_status(_: User = Depends(current_admin),
                 session: Session = Depends(get_session)):
    """Attempt counts per work state and harness, and live workers."""
    rows = session.exec(
        select(Submission.harness_id, Attempt.dispatch_state, func.count())
        .join(Submission, Submission.id == Attempt.submission_id)
        .where(Attempt.dispatch_state.isnot(None))
        .group_by(Submission.harness_id, Attempt.dispatch_state)).all()
    by_harness: dict = {}
    for hid, state, n in rows:
        by_harness.setdefault(hid, {})[state] = n
    with scheduler._lock:
        workers = {hid: sum(t.is_alive() for t in ts)
                   for hid, ts in scheduler._workers.items()}
    return {"enabled": scheduler.enabled(), "boot": scheduler.BOOT,
            "states": by_harness, "workers": workers}
