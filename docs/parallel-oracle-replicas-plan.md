# Implementation plan: parallel oracle execution via replica pools

Status: IMPLEMENTED, phases 0 to 3 (2026-10-12). The section "As built" at the
end records what changed against this plan, how to use it, and how it was
verified. The plan text below is kept as written.

## Goal

Register N interchangeable runners behind one logical oracle. Let the backend
hand each pending attempt to whichever runner is free. With N identical
machines, a sweep that runs serially today finishes about N times faster.

## The hard constraint that shapes the design

A runtime or energy number is only comparable within one
`(machine, energy_tool, config)` triple. This is the measurement-profile key:
`measurement_profile.config_hash` hashes `{energy_tool, config}`, and
`get_or_create_profile` dedups on `(machine_id, config_hash)`. A submission is
also pinned to one machine today. `dispatch._assign_machine` treats runs that
land on a second machine as a conflict and leaves `machine_id` unset.

So a naive fan-out across N machines breaks the benchmark. One model's runs
scatter across N machine profiles, and the leaderboard can no longer show one
comparable number. Parallelism is a comparability problem first and a
scheduling problem second.

Two cases split here:

- **Correctness oracle replicas — the easy case.** A verdict (Accepted / Wrong
  Answer / Runtime Error / Time Limit Exceeded) is deterministic and
  machine-independent. Replicas need no shared specs and no calibration. This
  is pure stateless fan-out.
- **Performance oracle replicas — the hard case.** Runtime and energy are
  machine-bound. Replicas must be proven equivalent and must pool into one
  measurement profile, or the parallel runs are not comparable.

## Design summary

Separate two things the schema conflates in one `Harness` row today:

- **The logical oracle.** The identity that `Submission` and `Run` reference,
  that the leaderboard groups on, and that defines a measurement profile. This
  stays exactly as it is.
- **The physical replicas.** A set of interchangeable runner endpoints behind
  that logical oracle. New, additive, and invisible to every existing read
  surface.

Add a **machine class**: a declared equivalence set of physically identical
machines (same model, same energy tool, same config) that may pool their
measurements. The profile key moves from the individual machine to the machine
class.

## Data model changes (additive, compat-preserving)

The same playbook as the agent change: new nullable columns through
`db._ADDITIVE_COLUMNS`, a seed-and-backfill for any default, and no change to
the trace hash or the model digest.

1. **`MachineClass` table** — `(id, slug UNIQUE, name, spec JSON, energy_tool,
   status, created_at)`. `Machine` gets a nullable `machine_class_id` FK.
2. **`OracleReplica` table** (name TBD, alt `RunnerEndpoint`) —
   `(id, harness_id FK NULL, correctness_oracle_id FK NULL, endpoint,
   auth_token, machine_id FK NULL, status, last_health_at, in_flight,
   created_at)`. Exactly one of `harness_id` / `correctness_oracle_id` is set.
   The logical oracle's existing `config.endpoint` becomes replica 0, so a
   one-runner oracle is a pool of size 1 and behaves as today.
3. **Profile re-keying, the compat-safe way.** `get_or_create_profile` keys on
   the machine's effective class: `machine_class_id` when set, else the bare
   `machine_id`. A machine with no class is its own singleton class, so the
   dedup key is byte-identical to today. Every existing profile and run stays
   valid, and no data is repopulated. This is the same "omit when empty →
   identical hash" trick that kept the agent change compatible.
4. **`_assign_machine` rule relaxed.** Runs on several machines are a conflict
   only if those machines are in different classes. Same-class runs are fine,
   and the submission's measurement identity becomes the class.

## Admission gate (the trust half of the performance case)

A declaration that "these N machines are identical" is not enough to pool
energy. Before a performance replica serves traffic:

1. Run a fixed reference solution on it (a known CLBG cell).
2. Admit it to the class only when its energy and runtime land within a
   tolerance band of the class baseline.
3. Record the calibration as provenance, and re-check it on a schedule.

A replica that drifts out of band is quarantined, not pooled. Correctness
replicas skip this step.

## The scheduler (the management logic)

Today `dispatch_submission` is a per-submission background task with a
sequential per-attempt loop against one fixed URL. Replace that with a
claim-based work queue at the attempt level.

- **Unit of work:** one attempt's measurement, with an explicit lifecycle:
  `pending → claimed(replica, lease_until) → done | failed`.
- **Atomic claim (SQLite-safe):** a worker claims the oldest pending attempt
  with a guarded update:
  `UPDATE attempt SET claimed_by=:replica, lease_until=:t
   WHERE id=(SELECT id FROM attempt WHERE state='pending' AND oracle=:oracle
             ORDER BY created_at LIMIT 1) AND state='pending'`.
  The `AND state='pending'` loses the race cleanly, so no two replicas grab the
  same attempt.
- **Worker pool:** one in-flight attempt per replica. The runner is
  single-threaded and already answers HTTP 429 when busy. Concurrency equals
  the number of healthy replicas.
- **Leases and idempotency:** a claim carries a lease. A dead replica or an
  expired lease returns the attempt to `pending`, and another replica takes it.
  Writes key on `attempt_id`, so a late reply from a dead lease cannot
  double-write a `Run`.
- **Fairness:** round-robin across submissions, so one large sweep does not
  starve another.
- **Back-pressure:** a 429 from a busy replica returns the attempt to the
  queue instead of blocking.

## Phased implementation

Each phase lands behind a flag and leaves the single-replica path unchanged.

### Phase 0 — correctness replicas (easy case)

Scope: fan out the validate step across replicas. No machine class, no
calibration.

- `models.py`: add `OracleReplica` (correctness side first).
- `db.py`: `_ADDITIVE_COLUMNS` for the replica table columns that need
  back-fill, and a seed that folds each oracle's existing `config.endpoint`
  into a replica 0 row.
- `main.py`: replica CRUD under `/api/admin/.../replicas`, and a read endpoint
  so the SPA can list the pool.
- `arena_local_runner.py`: `_client` takes an explicit endpoint, not only
  `config.endpoint`, so the caller can target one replica.
- `dispatch.py`: the validate path picks a free correctness replica instead of
  the fixed endpoint.
- Verify: register two correctness replicas locally and confirm two attempts
  validate at the same time, with identical verdicts.

### Phase 1 — MachineClass + profile re-keying (compat-critical)

Scope: the singleton-safe profile key, verified against the real DB.

- `models.py`: `MachineClass` + `Machine.machine_class_id`.
- `db.py`: additive column, and a seed that leaves every existing machine
  classless (singleton).
- `measurement_profile.py`: key on the effective class id.
- `dispatch.py`: relax `_assign_machine`.
- Verify (mirror the agent change): boot against a copy of the real DB, confirm
  every pre-existing `MeasurementProfile` hash and every `Run.measurement_profile_id`
  are unchanged, and confirm the leaderboard numbers do not move.

### Phase 2 — performance replicas + calibration gate

Scope: pool N identical machines behind one performance oracle.

- `models.py`: performance side of `OracleReplica`, plus a calibration record.
- `main.py`: the admission endpoint that runs the reference workload and the
  tolerance check.
- `dispatch.py`: `_assign_machine` sets the submission's identity to the class
  when the runs span same-class machines.
- Verify: register two performance replicas in one class, run a sweep, and
  confirm the runs pool into one profile and the per-model number is stable.

### Phase 3 — scheduler cutover

Scope: replace the per-submission loop with the claim-based queue.

- `models.py`: the attempt work-state columns
  (`state`, `claimed_by`, `lease_until`), additive and defaulted to the legacy
  behavior.
- `dispatch.py`: the claim loop, the worker pool, lease expiry, and requeue.
- Flag: the single-replica path stays on the old loop until the flag flips.
- Verify: kill a replica mid-sweep and confirm its leases requeue and the sweep
  finishes on the survivors, with no double-written `Run`.

## Backward compatibility guarantees

- A single-endpoint oracle is a pool of size 1 and behaves bit for bit as today.
- Every legacy row keeps its singleton machine class, its profile, and its hash
  chain. Nothing is recomputed.
- A replica going down drains its leases back to the queue and drops out of the
  healthy set. The rest keep serving.
- A `Run` still records the physical `machine_id` it ran on, even while the
  profile pools at the class level, so an auditor can always re-check class
  homogeneity after the fact.

## Local verification setup

- `docker-compose.local.yml`: add a second and third `harness` service on the
  internal network (same image, different service name) to stand in for
  replicas. Register them as replicas of the local `arena-local` oracle.
- Snapshot the DB before each schema phase, the same way the agent change took
  `arena.db.pre-agent.bak`.

## Open decisions (need Rui's call before Phase 1)

1. **Machine-class trust:** operator declaration only, or declaration plus the
   empirical calibration gate (recommended)?
2. **Parallelism granularity:** fan out within one submission across the pool
   (fastest, needs the same-class `_assign_machine` relaxation), or keep a
   submission on one replica and parallelize only across submissions (simpler,
   smaller compat surface)?
3. **Scheduler home:** an in-process worker pool over SQLite claims (least
   disruption, the default), or a separate dispatcher process?

## File-touch index

- `webapp/backend/app/models.py` — `MachineClass`, `OracleReplica`,
  `Machine.machine_class_id`, attempt work-state columns.
- `webapp/backend/app/db.py` — `_ADDITIVE_COLUMNS` entries, replica-0 seed,
  classless-machine seed.
- `webapp/backend/app/measurement_profile.py` — effective-class profile key.
- `webapp/backend/app/dispatch.py` — `_assign_machine` relaxation, replica
  selection, the claim-based queue.
- `webapp/backend/app/arena_local_runner.py` — per-replica endpoint in
  `_client`, `dispatch_via_arena_local`, `validate_via_arena_local`.
- `webapp/backend/app/main.py` — replica and machine-class CRUD, the admission
  endpoint.
- `webapp/backend/app/schemas.py` — replica, machine-class, calibration shapes.
- `docker-compose.local.yml` — extra runner services for local replica tests.

## As built (2026-10-12)

### Decisions

1. Machine-class trust: declaration plus the calibration gate.
2. Granularity: fan out within one submission, but pool only within one
   admitted machine class. Across classes, a submission stays on one machine.
3. Scheduler home: an in-process worker pool over SQLite claims.
4. Merge rule: pooled runs for performance; a unanimous verdict for
   correctness, else the attempt gets the verdict `Inconsistent verdict`.

### Files

- `app/replicas.py` (new): pool resolution, the primary replica, policies,
  endpoint slots (back-pressure), health, the comparability rule, merges,
  broadcast shadows, calibration, and the periodic recalibration.
- `app/scheduler.py` (new): the claim-based queue (Phase 3).
- `app/replica_api.py` (new): the HTTP endpoints, mounted by `main.py`.
- `app/dispatch.py`: the remote-runner body of the per-attempt loop moved
  unchanged into `_run_remote_attempt`, and the submission rollup into
  `_finalize_submission`, so the legacy loop and the queue share them.
  `_stamp_submission_provenance` accepts several machines of one class.
- `app/measurement_profile.py`: the profile key is the machine's class when
  it has one, else the machine (the legacy key).
- `app/models.py`, `app/db.py`: tables `machineclass`, `oraclereplica`,
  `replicacalibration`, `replicaresult`; nullable columns on `machine`,
  `measurementprofile`, `submission`, `run`, and `attempt`; the primary
  replica seed.
- `app/export_import.py`: machine classes travel by slug, the new FKs are
  remapped, and imported attempts never carry queue state.
- `docker-compose.local.yml`, `docker/harness-test/register-replicas.sh`.

### Policies

Set `config.replicas` on the harness or the correctness oracle, for example
`{"policy": "split_repeats"}` or `{"policy": "hedged", "hedge_after_s": 60}`.

| Policy | Stage | What it does |
|---|---|---|
| `primary` | both | The `config.endpoint` only. This is the legacy code path. |
| `round_robin` | both | Rotates over the free replicas. |
| `least_busy` | both | Free replica with the lowest load, then the lowest latency. |
| `weighted` | both | Smooth weighted round robin by `replica.weight`. |
| `sticky` | both | One replica per submission. |
| `failover` | both | First healthy replica by `priority`. The next one only after a failure, not while busy. |
| `hedged` | both | A second replica starts after `hedge_after_s`. The first answer wins. |
| `broadcast` | both | Every replica. The home replica's answer counts. The others go to `replicaresult`. |
| `broadcast_merge` | both | Every replica. Correctness needs a unanimous verdict. Performance pools all runs. |
| `quorum` | correctness | `quorum` replicas (default 3). The majority verdict counts. |
| `split_repeats` | performance | The N repeats are split over the replicas. The runs are pooled. |

With no policy set, a pool that has only the primary uses `primary`. A larger
pool uses `least_busy` for correctness and `sticky` for performance. The
setting `dispatch.replica_pools=0` forces `primary` everywhere.

Comparability rule (performance). A replica takes work for a submission only
if it measures on the same machine as the submission's earlier runs, or if
every machine involved is in one class and admitted to it. The rule applies to
every policy. A replica learns its machine from its first measurement or from
calibration. Until then, it only takes submissions that have no measured runs.

### Endpoints

- `GET /api/harnesses/{slug}/replicas`, `GET /api/correctness-oracles/{slug}/replicas`
- `POST /api/admin/replicas`, `PUT|DELETE /api/admin/replicas/{id}`,
  `POST /api/admin/replicas/{id}/probe`
- `POST /api/admin/replicas/{id}/calibrate` (runs in the background),
  `GET /api/admin/replicas/{id}/calibrations`
- `GET /api/admin/replicas/inconsistent` (correctness disagreements with the votes)
- `GET /api/machine-classes`, `POST /api/admin/machine-classes`,
  `PUT /api/admin/machine-classes/{id}`, `PUT /api/admin/machines/{id}/class`
- `GET /api/admin/queue`

New settings (Variables tab): `dispatch.replica_pools`,
`dispatch.replica_wait_s`, `dispatch.scheduler_queue` (default 0),
`dispatch.queue_max_tries`, and `replicas.recalibrate_interval_h`.

### Changes against the plan

- The replica table holds `weight`, `priority`, `capacity`, and
  `admitted_class_id`. `in_flight` lives in memory per endpoint, because one
  runner registered under a harness and a correctness oracle is one box.
- The queue claims a whole attempt (gate plus measure). A fan-out policy uses
  several replica slots inside one claim, so no separate task table is needed.
- Workers per harness = the total replica capacity (1 on the primary path).
- `Run` gained `replica_id` and `machine_id` for the physical provenance.

### Behavior fixed or kept on purpose

- Fixed: a transient correctness-gate failure also wrote a "Dispatch error"
  Run row, so the attempt left the pending set. It now writes zero rows, as
  its comment says.
- Kept on the legacy loop, changed in the queue: when the runner answers
  `/measure` with only system failures, the legacy remote path sets
  `final_status = "Dispatch error"` with no Run rows. The queue retries
  instead.
- Kept: on the real path, a prefailed attempt gets its aggregate but no Run
  row. The queue does the same.

### Verification

- Tests: `tests/test_replicas_correctness.py` (21), `test_replicas_performance.py`
  (18), `test_replica_scheduler.py` (10), and `test_replica_api.py` (8) run
  against real HTTP fake runners. `test_replica_compat.py` is opt-in:
  `ARENA_COMPAT_DB=<copy of arena.db> pytest tests/test_replica_compat.py`.
- On a copy of the deployed data (482 submissions, 20,708 attempts, 115,425
  runs), the upgrade keeps every row and column value. Every profile hash and
  every `Run.measurement_profile_id` stays the same. The old code on the old
  DB and the new code on the upgraded DB return identical JSON on 72 public
  read endpoints, including 286 ranking rows.
- End to end with two real runner processes (`perfarena_handler`): legacy
  loop, calibration through the API, the queue with `least_busy`, and the
  queue with `split_repeats`.
