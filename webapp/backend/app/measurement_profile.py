"""Helpers for MeasurementProfile — the (machine x energy tool x config) under
which a run was measured. `get_or_create_profile` dedups by (machine_id,
config_hash) so the same setup resolves to one row; the backend calls it when
recording runs and stamps Run.measurement_profile_id.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlmodel import Session, select

from .models import MeasurementProfile


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
) -> MeasurementProfile:
    """Return the existing profile for (machine_id, tool+config) or create it.
    Caller commits; this flushes so obj.id is available for FK stamping."""
    h = config_hash(energy_tool, config)
    existing = session.exec(
        select(MeasurementProfile).where(
            MeasurementProfile.machine_id == machine_id,
            MeasurementProfile.config_hash == h,
        )
    ).first()
    if existing is not None:
        return existing
    obj = MeasurementProfile(
        machine_id=machine_id, energy_tool=energy_tool,
        config=config or {}, config_hash=h,
    )
    session.add(obj)
    session.flush()
    return obj
