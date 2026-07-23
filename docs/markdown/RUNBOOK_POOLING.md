# Connection Pooling Topology — Runbook (P2-T09)

> Implements **ARCHITECTURE_DATA_PLATFORM.md §4.2**. Without pooling the cluster
> exhausts `max_connections` around ~20 active tenants; naive pooling silently
> kills Odoo's realtime bus. This is the exact split that avoids both.

## The topology

```
Odoo HTTP workers (odoo, :8069)      ──►  PgBouncer (transaction)  ──►  PostgreSQL
Odoo bus + cron (odoo-bus, :8072)    ─────────────── DIRECT ───────────►  PostgreSQL
queue_job runner (provisioning-runner) ───────────── DIRECT ───────────►  PostgreSQL
```

**Why split:** transaction pooling hands a server connection to a client only
for one transaction — perfect for stateless HTTP, fatal for anything needing a
**session**: `LISTEN/NOTIFY` (the chatter/presence bus), advisory locks, long
cron/queue transactions. So HTTP pools; bus/cron/queue stay direct.

`db_host` is static per Odoo process (§4.2), so the split is done by **running
two Odoo services** with different DB targets — not one process. The queue runner
(P2-T01) was already a separate direct container; this adds the bus/cron one.

## Files

| File | Role |
|---|---|
| `docker-compose.pooling.yml` | pgbouncer + odoo-bus (direct) + odoo→pgbouncer override |
| `config/pgbouncer/pgbouncer.ini` | transaction mode, wildcard tenant DBs, `SHOW POOLS` access |
| `config/pgbouncer/userlist.txt(.example)` | auth (real file gitignored, generated from `.env`) |
| `nginx/conf.d/ncollection.prod.conf` | `odoo_bus` upstream → `odoo-bus:8072` |

## Deploy (prod / staging — the standard prod topology)

```bash
# 1. Generate the gitignored auth file from .env (once):
printf '"%s" "%s"\n' "$DB_USER" "$DB_PASSWORD" > config/pgbouncer/userlist.txt
chmod 600 config/pgbouncer/userlist.txt

# 2. Bring the stack up WITH the pooling overlay (prod always layers it):
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
               -f docker-compose.pooling.yml up -d

# 3. Prove it:
./scripts/deploy/verify_pooling.sh
```

The prod nginx `odoo_bus` upstream points at `odoo-bus`, so **prod must layer the
pooling overlay** (otherwise nginx has no bus upstream). Add `-f
docker-compose.pooling.yml` to the staging deploy once the VPS edge is live.

## Monitoring (feeds P2-T10 / P8-T04)

```bash
# Pool saturation — watch cl_active vs sv_active vs maxwait:
docker compose ... exec db psql -h pgbouncer -p 6432 -U odoo -d pgbouncer -c "SHOW POOLS"
```

Alert when `maxwait > 0` (clients queuing for a server connection) or a pool sits
at `pool_size`. Stop one hot tenant starving the fleet with a per-DB override in
`pgbouncer.ini`: `clienta = host=db port=5432 pool_size=12`.

## Known notes

- The `odoo` (HTTP) service still spawns a gevent bus worker on its own :8072,
  but **nginx never routes bus traffic to it** (it goes to `odoo-bus`), so its
  pooled/broken LISTEN is harmless and idle.
- PostgreSQL: raise `max_connections` modestly (≈200); PgBouncer absorbs the
  client fan-in. Tune `shared_buffers` accordingly (P3-T02).

## Boundary (live proof)

`verify_pooling.sh` proves HTTP-through-PgBouncer + `SHOW POOLS` + the direct
bus/cron node locally. The full **chatter-delivers-over-nginx** proof needs the
real edge (nginx + wildcard cert) on the staging VPS (P2-T07) — same deferral as
the other infra tickets.

## OCA / reuse (Rule 2)

N/A — PgBouncer is infrastructure, not an Odoo module. The topology follows
Odoo's own reference guidance for pooling + the realtime bus split.
