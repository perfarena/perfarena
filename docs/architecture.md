# Architecture

This file shows how the parts of PerfArena work together. The sequence diagrams show the `leetcode` harness, where the LeetCode judge checks and measures each solution. With a local harness such as `arena-linux-local`, the dispatch worker first sends the solution to the correctness oracle, and then sends it to the harness runner on the measurement host (see [`deployment.md`](deployment.md), section 3).

## Architecture

PerfArena is a **single-container web app** (FastAPI serves both `/api/*` and the built React SPA) with a **persistent SQLite database** and an **async dispatch worker** that calls LeetCode through `leetbot.py`. External population scripts (`matrix_sweep.py`, `submit_with_local_model.py`) talk to the same public HTTP API real users do.

```mermaid
flowchart LR
    subgraph clients["Clients"]
        Browser["React SPA<br/>(browser)"]
        Sweeper["matrix_sweep.py<br/>(local script)"]
        Demo["submit_with_local_model.py"]
    end

    subgraph llm["LLM providers"]
        Ollama["Ollama<br/>(local or ngrok-tunneled)"]
        OtherLLM["any provider<br/>that emits a trace"]
    end

    subgraph container["PerfArena container :8000"]
        FastAPI["FastAPI app<br/>(uvicorn)"]
        Dispatch["dispatch worker<br/>(background thread)"]
        SQLite[("SQLite<br/>/data/arena.db")]
        Static["built SPA<br/>(served at /)"]
    end

    LC["LeetCode<br/>GraphQL + submit API"]

    Sweeper --> Ollama
    Demo --> Ollama
    Sweeper -.alternate.-> OtherLLM
    Browser --> Static
    Browser --> FastAPI
    Sweeper --> FastAPI
    Demo --> FastAPI
    FastAPI <--> SQLite
    Dispatch <--> SQLite
    Dispatch --> LC
    FastAPI -. spawns .-> Dispatch
```

**Key boundary properties:**

- **`/api/*`** is the **only contract** between population scripts and the platform. The sweep doesn't touch the DB; the DB doesn't touch the LLM.
- **Dispatch is async** — `POST /api/submissions/{id}/attempts` returns immediately with `queued`/`dispatching`; results land via polling `GET /api/attempts/{aid}`.
- **Migrations are additive-only** — `db.py:_ADDITIVE_COLUMNS` adds new columns on boot, never drops data. Re-deploys preserve the DB.
- **Rate limiting** has two layers: per-(IP, endpoint) GETs (default 60/min, see `ip_ratelimit.py`) and per-user submission cooldown + daily cap (see `ratelimit.py`).
- **Provenance** is a SHA-256 hash chain (`provenance.py`) of `(code, inference_trace, trace)`; clients can optionally Ed25519-sign each leaf.

## End-to-end workflows

### Population: matrix sweep → ranked leaderboard

Single-shot mode (`--rounds 1`, the default). Each `(prompt, language, problem)` tuple is one attempt.

```mermaid
sequenceDiagram
    autonumber
    participant Sweep as matrix_sweep.py
    participant Ollama
    participant API as FastAPI<br/>/api/*
    participant DB as SQLite
    participant Dispatch as dispatch worker
    participant LC as LeetCode

    Sweep->>API: GET /api/problems/{slug} (with Bearer)
    API->>DB: read problem + per-lang snippet
    DB-->>API: rows
    API-->>Sweep: ProblemDetail JSON

    Sweep->>Sweep: build prompt from template <br/> expand starter_code, hints, target placeholders
    Sweep->>Ollama: POST /api/chat with think=true
    Ollama-->>Sweep: message + reasoning + provider_meta

    Sweep->>Sweep: extract code block, run language fingerprint check, <br/> compute trace_hash

    alt first tuple in this prompt+language cell
        Sweep->>API: POST /api/submissions <br/> prompt_id, language, model_params, attempts
        API->>DB: insert Submission + Attempt with status=queued
        API->>Dispatch: schedule dispatch
        API-->>Sweep: 201 with submission_id + attempts
    else cell already exists
        Sweep->>API: POST /api/submissions/{id}/attempts <br/> append-if-existing
        API->>DB: insert Attempt with status=queued
        API->>Dispatch: schedule dispatch
        API-->>Sweep: 201 with attempt_id
    end

    par dispatch worker async
        Dispatch->>DB: pop queued attempt
        Dispatch->>LC: POST /problems/{slug}/submit/ <br/> via leetbot.py
        LC-->>Dispatch: submission_id
        loop poll
            Dispatch->>LC: GET /submissions/detail/{id}/check/
            LC-->>Dispatch: STARTED / SUCCESS / WRONG_ANSWER / …
        end
        Dispatch->>LC: GraphQL submissionDistribution for baselines
        LC-->>Dispatch: runtime + memory histograms
        Dispatch->>DB: update Attempt with ms, mb, percentile, status, raw_response
    and sweeper polls
        Sweep->>API: GET /api/attempts/{aid}
        API->>DB: read attempt
        DB-->>API: row with status, ms, mb, percentile
        API-->>Sweep: AttemptDetail JSON
        Note over Sweep: keep polling until status is <br/> accepted / wrong_answer / runtime_error / …
    end

    Sweep->>Sweep: write progress log <br/> resumable on restart
```

After every attempt resolves, aggregates are derived on read:

- **Per-attempt aggregate** — `best_runtime_ms`, `best_memory_mb`, `best_runtime_percentile`, etc. over its retries.
- **Leaderboard** — `pass@k`, mean best runtime, mean best memory, mean best percentile, per (model, language, level).
- **Per-problem comparison** — bar widths scaled as `(min / value) × track_width` so the winner fills the track; bigger bar = better on every metric.

### Iterative refinement (multi-round) sweep

`--rounds N` (N>1) uses the `iterative-refine[-with-history][-with-shape]` prompt family. Round 0 is the cold attempt; rounds 1..N-1 are generated with `{{rounds_history}}` containing the previous judge verdict.

```mermaid
sequenceDiagram
    autonumber
    participant Sweep as matrix_sweep.py<br/>(--rounds 3)
    participant Refine as iterative_refine.py
    participant Ollama
    participant API
    participant Dispatch
    participant LC as LeetCode

    Note over Sweep: Round 0 — cold start
    Sweep->>Ollama: chat with prompt for round 0
    Ollama-->>Sweep: code0
    Sweep->>API: POST attempt with round_index=0, n_rounds=3
    API->>Dispatch: queue
    Dispatch->>LC: submit + poll
    LC-->>Dispatch: WRONG_ANSWER on testcase X
    Dispatch-->>API: update attempt
    Sweep->>API: poll for result

    alt accepted
        Note over Sweep: STOP early — record solved_at_round=0
    else not accepted
        Note over Sweep: Round 1
        Sweep->>Refine: distill_judge_result for attempt0
        Sweep->>Refine: build_rounds_history from round 0 verdict
        Sweep->>Refine: build_round_instruction for round 1 of 3
        Sweep->>Ollama: chat with prompt + rounds_history + round_instruction
        Ollama-->>Sweep: code1
        Sweep->>API: POST attempt with round_index=1, previous_attempt_id=aid0
        API->>Dispatch: queue
        Dispatch->>LC: submit + poll
        LC-->>Dispatch: ACCEPTED
        Dispatch-->>API: update attempt
        Sweep->>API: poll, now accepted
        Note over Sweep: STOP — record solved_at_round=1
    end

    Sweep->>Sweep: log rounds_used=2, solved_at_round=1
```

**Why rounds aren't just pass@k:** pass@k samples k *independent* tries from the same prompt; rounds give the model the *judge's verdict on the previous try* so the next attempt can debug. The taxonomy lives in `iterative_refine.py` and is surfaced on the leaderboard as `solved_at_round` and `rounds_used`.

### Read path: user opens the leaderboard

```mermaid
sequenceDiagram
    autonumber
    participant Browser
    participant SPA as React SPA<br/>(served at /)
    participant API
    participant DB as SQLite
    participant IPL as ip_ratelimit<br/>(middleware)

    Browser->>SPA: GET / — PWA shell from SW if cached
    SPA->>API: GET /api/rankings?language=python3&level=Medium
    API->>IPL: check_get_request
    IPL-->>API: ok — under 60/min for this IP+path
    API->>DB: SELECT … FROM Attempt JOIN Submission … <br/> GROUP BY model, language, level
    DB-->>API: aggregated rows
    API-->>SPA: RankingsResponse JSON
    SPA->>SPA: render table with inline SVG bars, no chart lib

    Note over Browser,SPA: User clicks a model → /models/{name}
    SPA->>API: GET /api/models/{name}
    API->>DB: per-problem aggregates for this model
    DB-->>API: rows
    API-->>SPA: ModelDetail JSON
    SPA->>SPA: render histogram via CompareHistogram.tsx
```

### Provenance verification

Anyone can recompute the trace hash and walk the chain to detect tampering:

```mermaid
sequenceDiagram
    autonumber
    participant Auditor as 3rd-party auditor
    participant API
    participant DB

    Auditor->>API: GET /api/submissions/{id}
    API->>DB: read submission + attempts incl. provenance fields
    DB-->>API: rows
    API-->>Auditor: JSON with code, inference_trace, trace, <br/> trace_hash, sig_status, model_digest, log_seq

    Auditor->>Auditor: recompute sha256 over <br/> canonical_json of code+inference_trace+trace
    Auditor->>Auditor: compare against returned trace_hash

    Auditor->>API: GET /api/provenance/log?since=0
    API->>DB: read append-only log
    DB-->>API: entries with seq, trace_hash, prev_hash, signed_by
    API-->>Auditor: hash chain
    Auditor->>Auditor: walk chain — each entry's prev_hash must match <br/> the previous entry's hash, else tamper detected

    opt signed attempts
        Auditor->>Auditor: verify Ed25519 signature over trace_hash with pubkey
    end
```

`canonical_json` is `json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=False)` — reproduce it exactly or the hash won't match.
