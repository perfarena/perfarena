"""Helpers for MeasurementProfile — the (machine x energy tool x config) under
which a run was measured. `get_or_create_profile` dedups by (machine_id,
config_hash) so the same setup resolves to one row; the backend calls it when
recording runs and stamps Run.measurement_profile_id.

A machine in a MachineClass keys on its class instead: (machine_class_id,
config_hash), so identical machines pool into one profile. A machine with no
class is its own singleton class, so its key stays (machine_id, config_hash)
and every pre-existing profile resolves exactly as before.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlmodel import Session, select

from .models import Machine, MeasurementProfile


def config_hash(energy_tool: str | None, config: dict[str, Any] | None) -> str:
    """sha256 over canonical JSON of {energy_tool, config}. Machine-independent
    (the machine is keyed separately), so the same tool+config on two boxes
    hashes the same and dedup keys on (machine_id, config_hash)."""
    payload = json.dumps(
        {"energy_tool": energy_tool, "config": config or {}},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_or_create_profile(
    session: Session, machine_id: int | None,
    energy_tool: str | None, config: dict[str, Any] | None,
    *, machine_class_id: int | None = None,
) -> MeasurementProfile:
    """Return the existing profile for (effective class, tool+config) or
    create it. The effective class is `machine_class_id` when given, else the
    machine's own class, else the bare machine. Caller commits; this flushes
    so obj.id is available for FK stamping."""
    h = config_hash(energy_tool, config)
    if machine_class_id is None and machine_id is not None:
        m = session.get(Machine, machine_id)
        machine_class_id = m.machine_class_id if m is not None else None
    if machine_class_id is not None:
        key = (MeasurementProfile.machine_class_id == machine_class_id,)
        new = {"machine_id": None, "machine_class_id": machine_class_id}
    else:
        key = (MeasurementProfile.machine_id == machine_id,)
        new = {"machine_id": machine_id}
    existing = session.exec(
        select(MeasurementProfile).where(
            *key, MeasurementProfile.config_hash == h,
        )
    ).first()
    if existing is not None:
        return existing
    obj = MeasurementProfile(
        **new, energy_tool=energy_tool, config=config or {}, config_hash=h,
    )
    session.add(obj)
    session.flush()
    return obj
