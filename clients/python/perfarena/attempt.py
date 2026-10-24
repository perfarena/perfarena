"""The attempt body sent to POST /api/submissions/{id}/attempts. Same rules
as matrix_sweep.py: compile-flag precedence, the inference_trace summary, the
token counts (provider usage, else an estimate), cost, and the refinement
chain link. Signing is optional (the scripts' --sign-key)."""
from __future__ import annotations

from dataclasses import dataclass

from .codegen import extract_compile_flags, sanitize_compile_flags
from .pricing import PriceBook, estimate_code_tokens
from .provenance import sign_leaf, trace_hash


@dataclass
class BuiltAttempt:
    body: dict
    leaf_hash: str


def build_attempt(code: str, trace: dict, *, slug: str, language: str, model: str,
                  model_version: str, pricebook: PriceBook,
                  prefailed_reason: str | None = None, round_index: int = 0,
                  previous_attempt_id: int | None = None,
                  model_digest: str | None = None, operator_flags: str = "",
                  sign_key: str | None = None) -> BuiltAttempt:
    # Model-chosen flags (a PERFARENA-FLAGS line) win over the operator's.
    model_flags = sanitize_compile_flags(extract_compile_flags(trace.get("raw_response") or ""))
    flags = model_flags or sanitize_compile_flags(operator_flags)
    if flags:
        trace["compile_flags"] = flags
        if model_flags:
            trace["compile_flags_source"] = "model"

    summary = (trace.get("reasoning") or trace.get("raw_response")
               or "see structured trace").strip()[:4000] or "x"
    if prefailed_reason and prefailed_reason.startswith("generation_"):
        summary = (f"client flagged {prefailed_reason}: the provider call failed, so the "
                   "model returned no code; the error is in trace.provider_meta")
    elif prefailed_reason and len(summary) < 20:
        summary = (f"client flagged {prefailed_reason}; raw response is in "
                   "trace.raw_response")
    elif len(summary) < 20:
        summary = f"{summary} [{model} · {language}/{slug}; full trace in trace.raw_response]"
    leaf = trace_hash(code, summary, trace)

    usage = (trace.get("provider_meta") or {}).get("usage") or {}
    body = {
        "title_slug": slug, "code": code, "inference_trace": summary, "trace": trace,
        "cost_usd": pricebook.cost_usd(trace, model_version),
        "response_tokens": (int(usage["completion_tokens"])
                            if usage.get("completion_tokens") is not None
                            else estimate_code_tokens(code)),
        "prompt_tokens": (int(usage["prompt_tokens"])
                          if usage.get("prompt_tokens") is not None else None),
        "wall_clock_ms": trace.get("wall_clock_ms"),
        "generation_energy_j": trace.get("energy_j"),
        "round_index": round_index,
    }
    if previous_attempt_id is not None:
        body["previous_attempt_id"] = previous_attempt_id
    if prefailed_reason:
        body["prefailed_reason"] = prefailed_reason
    if model_digest:
        body["model_digest"] = model_digest
    if sign_key:
        body["trace_sig"], body["trace_signer"] = sign_leaf(sign_key, leaf)
    return BuiltAttempt(body=body, leaf_hash=leaf)
