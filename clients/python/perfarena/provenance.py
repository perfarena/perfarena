"""Provenance helpers; must mirror webapp/backend/app/provenance.py.

The leaf hash is sha256 over canonical JSON of {code, inference_trace,
trace}. The server recomputes it and anchors it in its append-only log, so
the two must match byte for byte. Signing is optional (Ed25519, needs the
`cryptography` package: pip install perfarena[sign])."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from .errors import SpecError


def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def trace_hash(code: str, inference_trace: str, trace: dict | None) -> str:
    leaf = {"code": code, "inference_trace": inference_trace, "trace": trace}
    return hashlib.sha256(canonical_json(leaf)).hexdigest()


def _crypto():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ModuleNotFoundError as e:
        raise SpecError("signing needs the `cryptography` package "
                        "(pip install perfarena[sign])") from e
    return serialization, Ed25519PrivateKey


def sign_leaf(key_path: str | Path, leaf_hash: str) -> tuple[str, str]:
    """(signature_b64, public_key_b64) over the leaf hash."""
    serialization, Ed25519PrivateKey = _crypto()
    priv = serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None)
    if not isinstance(priv, Ed25519PrivateKey):
        raise SpecError(f"{key_path} is not an Ed25519 private key")
    sig = priv.sign(leaf_hash.encode("ascii"))
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw,
                                         serialization.PublicFormat.Raw)
    return base64.b64encode(sig).decode(), base64.b64encode(pub).decode()


def gen_key(path: str | Path) -> None:
    serialization, Ed25519PrivateKey = _crypto()
    pem = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())
    Path(path).write_bytes(pem)
