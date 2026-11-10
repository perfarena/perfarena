# Deploying the replica-pool version

This version adds replica pools, machine classes, calibration, and the
claim-based dispatch queue. The
upgrade itself needs no new configuration. With the defaults, every oracle
keeps its single endpoint and dispatch runs exactly as before. Sections 3 to 5
are optional: they turn the new features on.

The commands assume the production layout of the README ("Deployment
(hardened)"): the backend container `perfarena-backend` with `--network host`,
the database in the `perfarena-data` volume at `/data/arena.db`, and the runner
on the measurement host (bicho).

## 1. Upgrade the backend

1. Back up the database. The SQLite backup API gives a consistent copy while
   the app runs:

   ```bash
   docker exec perfarena-backend python -c "
   import sqlite3
   src = sqlite3.connect('file:/data/arena.db?mode=ro', uri=True)
   dst = sqlite3.connect('/data/arena.db.pre-replicas.bak')
   src.backup(dst); dst.close(); src.close()"
   ```

2. Note the current container's environment, then build the new image and
   replace the container with the same flags (README section 2):

   ```bash
   docker inspect perfarena-backend --format '{{range .Config.Env}}{{println .}}{{end}}' \
     | grep '^ARENA_' | cut -d= -f1          # names only; keep the values as they are
   git pull
   docker build -t perfarena-backend:latest .
   docker stop perfarena-backend && docker rm perfarena-backend
   docker run -d --name perfarena-backend --network host \
     -v perfarena-data:/data -e ARENA_DB=/data/arena.db \
     -e ARENA_DISPATCH_ENABLED=1 \
     -e ARENA_SESSION_SECRET='<the same secret as before>' \
     <every other -e flag the old container had> \
     --restart unless-stopped perfarena-backend:latest
   ```

   Keep `ARENA_SESSION_SECRET` the same, or every SPA login ends.

3. On boot, the backend migrates the database by itself. It adds four tables
   (`machineclass`, `oraclereplica`, `replicacalibration`, `replicaresult`)
   and nullable columns on `machine`, `measurementprofile`, `submission`,
   `run`, and `attempt`. Then it creates one primary replica for each runner
   oracle from its `config.endpoint`. It changes no existing value. A copy of
   the production data was upgraded this way, and 72 public endpoints
   returned identical JSON before and after.

## 2. Check the deployment

```bash
KEY=$(cat ~/.perfarena_admin_key)          # admin API key, never echo it
B=http://127.0.0.1:8000
curl -s $B/api/version
curl -s $B/api/harnesses/arena-linux-local/replicas      # one row, is_primary=true
curl -s $B/api/correctness-oracles/arena-local/replicas  # one row, is_primary=true
curl -s -H "Authorization: Bearer $KEY" $B/api/admin/queue   # "enabled": false
```

The admin Variables tab now has a "Replicas" group with these defaults:

| Setting | Default | Meaning |
|---|---|---|
| `dispatch.replica_pools` | 1 | Use registered replicas. Has no effect while each oracle has only its primary. |
| `dispatch.replica_wait_s` | 900 | How long a dispatch waits for a free replica. |
| `dispatch.scheduler_queue` | 0 | The claim-based queue is off. The legacy loop runs. |
| `dispatch.queue_max_tries` | 5 | Queue retries per attempt before it is parked with a warning. |
| `replicas.recalibrate_interval_h` | 24 | Re-check admitted performance replicas. Only classes with admitted replicas are touched. |

Run one small sweep (one model, one language, a few problems) and check that
attempts get verdicts as before.

The runner needs no change for this version, because the runner protocol is
the same. Only the `Energy-Languages` submodule pointer moved (to `8dd29803`,
which bicho already has).

## 3. Optional: add a correctness replica

A correctness verdict does not depend on the machine, so no calibration is
needed.

1. Set up the new runner host as in README section 3 (toolchains, the
   `perfrunner` user, the egress rule, `--auth-token`). Bind an address the
   backend can reach.
2. Register it:

   ```bash
   curl -s -X POST -H "Authorization: Bearer $KEY" -H 'content-type: application/json' \
     $B/api/admin/replicas -d '{
       "correctness_oracle_slug": "arena-local", "name": "runner-2",
       "endpoint": "http://<runner-2-ip>:8787", "auth_token": "<runner-2 token>"}'
   curl -s -X POST -H "Authorization: Bearer $KEY" $B/api/admin/replicas/<id>/probe
   ```

   With two active replicas and no policy set, the oracle uses `least_busy`.
   To choose another policy, edit the oracle's config on the Oracles page,
   for example `"replicas": {"policy": "broadcast_merge"}`. With that policy,
   every replica must agree, and disagreements are listed at
   `GET /api/admin/replicas/inconsistent`.

## 4. Optional: add a performance replica

There are two cases.

**Different hardware.** Register the replica with `"harness_slug"` instead of
`"correctness_oracle_slug"` and keep the default policy, `sticky`. Each
submission then stays on one machine. Leaderboard numbers stay comparable
within a submission, and the submissions spread over the machines.

**Identical machines (pooling).** Runs of one submission may then come from
several machines. This needs a machine class and a calibration for each
machine:

1. Pick a reference attempt: an Accepted solution that runs a few seconds,
   for example one CLBG C cell. Note its attempt id.
2. Create the class:

   ```bash
   curl -s -X POST -H "Authorization: Bearer $KEY" -H 'content-type: application/json' \
     $B/api/admin/machine-classes -d '{
       "slug": "bicho-class", "name": "bicho hardware",
       "calibration": {"attempt_id": <id>, "repeats": 3, "tolerance_pct": 5}}'
   ```

3. Calibrate the current primary first. Its result becomes the class
   baseline. Then calibrate each new replica. Each call runs in the
   background, so poll for the result:

   ```bash
   curl -s -X POST -H "Authorization: Bearer $KEY" -H 'content-type: application/json' \
     $B/api/admin/replicas/<primary id>/calibrate -d '{"machine_class_slug": "bicho-class"}'
   curl -s -H "Authorization: Bearer $KEY" $B/api/admin/replicas/<primary id>/calibrations
   ```

   A replica out of tolerance is quarantined and takes no performance work.
   To re-base the class, calibrate with `"set_baseline": true`.

4. Set the harness policy on the Harnesses page, for example
   `"replicas": {"policy": "split_repeats"}` or `{"policy": "least_busy"}`.

Note on data: once a machine is declared in a class, its new runs use a
class-level measurement profile. Its earlier runs keep their per-machine
profile. The leaderboard does not group by profile, but paper scripts that
key on `measurement_profile_id` should allow for both.

## 5. Optional: turn on the claim-based queue

Set `dispatch.scheduler_queue` to 1 in the Variables tab. No restart is
needed: the next dispatch of a runner-oracle submission goes to the queue,
with one worker per replica slot. LeetCode-harness submissions keep the
legacy loop. `GET /api/admin/queue` shows the attempts per state and the live
workers. Set the value back to 0 to return to the legacy loop. Attempts still
queued are then picked up by the next dispatch call of their submission.

## Client helper endpoints

The same backend version also serves `GET /api/attempts/{id}`,
`PUT /api/submissions/upsert`, and `GET /api/pricing` for the `perfarena`
SDK and its MCP server (`clients/python/`). They are additive: no setting,
no migration, and older clients are unaffected.

## Roll back

- To turn off the new behavior, set `dispatch.replica_pools=0` (primary
  endpoint only) and `dispatch.scheduler_queue=0`.
- To go back to the previous image, redeploy it on the same volume. The schema
  changes are additive, so the old code ignores the new tables and columns.
- To restore the data:

  ```bash
  docker stop perfarena-backend
  docker run --rm -v perfarena-data:/data alpine \
    sh -c 'cp /data/arena.db.pre-replicas.bak /data/arena.db && rm -f /data/arena.db-wal /data/arena.db-shm'
  docker start perfarena-backend
  ```

## Security

Every replica is a runner that executes untrusted code. Give each non-loopback
replica its own `--auth-token` and set it on the replica (`auth_token`), or
let it inherit the oracle's `config.auth_token`. The API never returns a
replica token: `has_auth_token` only says whether one is set. Replica rows are
site configuration, so database exports leave them out.

## Local stack

```bash
docker compose -f docker-compose.local.yml build
docker compose -f docker-compose.local.yml --profile replicas up -d
./docker/harness-test/register-harness.sh
./docker/harness-test/register-replicas.sh
```

Without `--profile replicas`, the compose stack is the same as before.
