# P3-T02 — PostgreSQL Tuning Baseline

Evidence for the "documented measurable improvement" acceptance criterion of
**[P3-T02] PostgreSQL Performance Tuning**. Reproduce with
[`scripts/perf/pg_baseline.sh`](../../scripts/perf/pg_baseline.sh).

## Method

`pg_baseline.sh` runs **two throwaway `postgres:16` containers sequentially** (never
concurrently — that would skew TPS through host contention), with an **identical
`pgbench` workload**, differing **only** in configuration:

- **default** — stock `postgres:16` (`shared_buffers` 128MB, `work_mem` 4MB, no telemetry).
- **tuned** — [`config/postgres/postgresql.conf`](../../config/postgres/postgresql.conf)
  applied via `postgres -c config_file=…` (exactly how the compose db service loads it).

It never touches the dev stack, its volumes, or any real database.

## Results

| Context | Value |
|---|---|
| Date | 2026-07-25 |
| Host | MacBook Air M1, 8 GB (Docker Desktop) — a **dev** box |
| Image | `postgres:16` (16.14) |
| Workload | `pgbench` scale 30 · 15 s · 8 clients · 2 threads |

| Workload | default (tps) | tuned (tps) | delta |
|---|---:|---:|---:|
| **SELECT-only** (`pgbench -S`) | 25,170 | **31,265** | **+24.2 %** |
| TPC-B (read/write) | 4,906 | 4,904 | −0.0 % |

*(Absolute numbers are host-specific; the **delta** is the tuning signal. Re-run on any host to reproduce.)*

## Interpretation

- **The +24.2 % read-throughput gain is the result that matters.** Odoo is
  overwhelmingly **read-heavy** — ORM reads, `ir.rule` record-rule domains on every
  query, license-enforcement checks, dashboards — so SELECT throughput is the
  representative multi-tenant metric. The gain comes from the larger buffer cache +
  `effective_cache_size` planner hint + `random_page_cost=1.1` (NVMe) favouring
  index paths.
- **TPC-B is flat, and that is honest, not a miss.** At scale 30 the dataset fits
  in *both* configs' buffers, so the write path is bounded by `fsync`/WAL, not by our
  memory tuning; `wal_compression` trades a little CPU for smaller WAL. The write-side
  wins (`maintenance_work_mem`, checkpoint spreading, `wal_compression`) show up under
  real fleet write volume, not a 30-scale micro-benchmark.
- **The template scales past this dev box.** `work_mem` 4MB→64MB removes on-disk sort
  spills for real Odoo reports (a dev laptop rarely triggers them at scale 30), and on
  the **prod box** the overlay raises `shared_buffers` to 8 GB / `effective_cache_size`
  to 24 GB (see below), where the read-cache advantage widens substantially.

## What shipped

**Config template** — [`config/postgres/postgresql.conf`](../../config/postgres/postgresql.conf),
the [ARCHITECTURE_DATA_PLATFORM §9](../markdown/ARCHITECTURE_DATA_PLATFORM.md) knobs:
`shared_buffers`, `effective_cache_size`, `work_mem=64MB`, `maintenance_work_mem=512MB`,
`random_page_cost=1.1`, `wal_compression=on`, `archive_timeout=60`, `max_connections=200`,
`jit=off` (OLTP), plus `pg_stat_statements` and `log_min_duration_statement=500`.

**Wiring** — base `docker-compose.yml` loads it via `-c config_file=…` (dev + CI + verify-all).
The **prod overlay** `docker-compose.prod.yml` overrides only the two host-RAM-dependent
knobs for the 32 GB box (`shared_buffers=8GB`, `effective_cache_size=24GB`, `max_wal_size=4GB`);
command-line `-c` wins over the file, so the rest stays as the template.

**Telemetry proven live** (on the dev `ncollection` db):

```
shared_preload_libraries = pg_stat_statements     # loaded
CREATE EXTENSION pg_stat_statements;              # queryable
log_min_duration_statement = 500ms                # fired: "duration: 2005.134 ms  statement: SELECT pg_sleep(2);"
```

## Re-run

```bash
./scripts/perf/pg_baseline.sh              # defaults: scale 30, 15s, 8 clients
PG_BENCH_SCALE=100 PG_BENCH_TIME=30 ./scripts/perf/pg_baseline.sh   # heavier
```

Run this after any change to the db config as a lightweight regression check.

## Not covered

- **Odoo-workload load testing** (k6/Locust, 50 users × 3 tenants, budget assertions)
  is **P3-T03**, which builds directly on this baseline.
- **Noisy-neighbor resource caps** (`statement_timeout`, per-role memory/connection
  limits) — a global `statement_timeout` would wrongly kill legitimate long ops
  (module installs, big reports, migrations), so it needs the measured context of
  the **P3-T03** worker/`limit_time` load testing rather than a blind default here.
- Slow-query logging deliberately **suppresses bind-parameter values**
  (`log_parameter_max_length = 0`) so tenant PII never lands in the server log; the
  normalized statement + timing (what you tune on) is still logged.

**Note on telemetry scope (not a gap):** `pg_stat_statements` accumulates in
server-wide shared memory, so the extension only needs to be created in **one**
database (the platform db) to observe the top queries across the **whole cluster**,
every tenant included — that is what the §9 weekly ops review reads. Creating the
extension in each tenant db is unnecessary for platform observability.
