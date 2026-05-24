"""Trace provenance: content-binding hashes, an append-only transparency log,
optional submitter signatures, and a provider-agnostic model digest.

What this gives you (and what it does NOT):

  * INTEGRITY — once a trace is logged we can prove it was not altered,
    reordered, or back-dated (e.g. rewritten after seeing which tests passed).
  * ACCOUNTABILITY — a submitter who registers a key signs their traces, so a
    trace is cryptographically bound to that account.
  * MODEL IDENTITY — every attempt carries a stable digest of the claimed
    model identity (an explicit one if the submitter supplies it, otherwise a
    deterministic one derived from name/version/params/provider metadata),
    provider-agnostic — Ollama, OpenAI, Anthropic, local HF, anything.

It does NOT prove the named model produced the text — only re-inference or
attested execution can do that. This is the integrity/accountability layer.

`canonical_json` is the contract: the platform and any client must serialise
identically (sorted keys, compact separators, UTF-8) or hashes won't match.
"""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime

from sqlmodel import Session, select

from .models import TraceLogEntry

GENESIS = "0" * 64


def canonical_json(obj) -> bytes:
    """Deterministic serialisation. Keep this byte-for-byte stable forever:
    clients reproduce it to verify the hashes the platform reports back."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------- content-binding leaf hash ---------------------------------------

def compute_trace_hash(code: str, inference_trace: str, trace: dict | None) -> str:
    """Bind code ⇄ summary ⇄ structured trace into one leaf hash.

    Changing the submitted code, the human summary, or any byte of the
    structured trace changes this hash — so a hand-edited solution can't be
    passed off as the model's, and the trace can't be swapped after the fact.
    """
    leaf = {"code": code, "inference_trace": inference_trace, "trace": trace}
    return _sha256_hex(canonical_json(leaf))


# ---------- provider-agnostic model digest ----------------------------------

def derive_model_digest(
    model_name: str,
    model_version: str,
    model_params: dict | None,
    trace: dict | None,
    supplied: str | None,
) -> tuple[str, str]:
    """Return (digest, source).

    If the submitter supplied an explicit digest (Ollama sha256, HF revision
    SHA, a provider system_fingerprint, …) we record it verbatim — that's the
    strongest identity claim and what re-inference would check against.

    Otherwise we derive a deterministic digest from a normalised identity
    bundle. This works for ANY provider: we opportunistically fold in well
    known fingerprint fields if the trace carries them.
    """
    if supplied:
        s = str(supplied).strip()
        if s:
            return s, "submitted"

    trace = trace or {}
    meta = trace.get("provider_meta") if isinstance(trace.get("provider_meta"), dict) else {}
    # Opportunistic, provider-agnostic fingerprint fields.
    fingerprint = {
        k: v for k, v in {
            "ollama_digest": trace.get("model_digest") or meta.get("digest"),
            "openai_system_fingerprint": meta.get("system_fingerprint"),
            "hf_revision": trace.get("revision") or meta.get("revision"),
            "response_id": meta.get("id") or meta.get("response_id"),
            "quantization": trace.get("quantization") or meta.get("quantization"),
        }.items() if v
    }
    identity = {
        "model_name": model_name,
        "model_version": model_version,
        "trace_model": trace.get("model"),
        "provider": trace.get("provider"),
        "endpoint": trace.get("endpoint"),
        "params": model_params or trace.get("params") or {},
        "fingerprint": fingerprint,
    }
    return "derived:" + _sha256_hex(canonical_json(identity)), "derived"


# ---------- optional submitter signature ------------------------------------

def verify_signature(
    pubkey_b64: str | None, sig_b64: str | None, leaf_hash: str,
) -> str:
    """Verify an Ed25519 signature over the leaf hash.

    Returns one of: 'verified' | 'invalid' | 'unsigned' | 'unsupported'.
    Unsigned traces are still logged & chained — they just carry less weight.
    """
    if not sig_b64 or not pubkey_b64:
        return "unsigned"
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
    except ModuleNotFoundError:
        return "unsupported"
    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(pubkey_b64))
        pub.verify(base64.b64decode(sig_b64), leaf_hash.encode("ascii"))
        return "verified"
    except InvalidSignature:
        return "invalid"
    except Exception:
        return "invalid"


# ---------- append-only transparency log ------------------------------------

def append_to_log(session: Session, attempt_id: int, leaf_hash: str) -> TraceLogEntry:
    """Append a hash-chained, server-timestamped entry. Append-only: each entry
    commits to the previous one, so the whole log is tamper-evident and the
    server timestamp can't be back-dated without breaking every later link."""
    last = session.exec(
        select(TraceLogEntry).order_by(TraceLogEntry.seq.desc())
    ).first()
    seq = (last.seq + 1) if last else 1
    prev_hash = last.entry_hash if last else GENESIS
    logged_at = datetime.utcnow()
    entry_hash = _sha256_hex(canonical_json({
        "seq": seq,
        "prev_hash": prev_hash,
        "leaf_hash": leaf_hash,
        "attempt_id": attempt_id,
        "logged_at": logged_at.isoformat(),
    }))
    entry = TraceLogEntry(
        seq=seq, attempt_id=attempt_id, leaf_hash=leaf_hash,
        prev_hash=prev_hash, entry_hash=entry_hash, logged_at=logged_at,
    )
    session.add(entry)
    return entry


def verify_chain(entries: list[TraceLogEntry]) -> bool:
    """Recompute the chain to confirm it hasn't been tampered with."""
    prev = GENESIS
    for e in sorted(entries, key=lambda x: x.seq):
        expect = _sha256_hex(canonical_json({
            "seq": e.seq,
            "prev_hash": prev,
            "leaf_hash": e.leaf_hash,
            "attempt_id": e.attempt_id,
            "logged_at": e.logged_at.isoformat(),
        }))
        if e.prev_hash != prev or e.entry_hash != expect:
            return False
        prev = e.entry_hash
    return True
