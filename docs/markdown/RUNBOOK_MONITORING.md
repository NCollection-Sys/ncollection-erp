# Platform Uptime Monitoring & Alerting — Runbook (P2-T10)

> **Lightweight monitoring NOW.** The full Prometheus + Grafana stack is
> **P8-T04**; this is the cheap cron-based safety net so the first production
> tenants (end of Phase 2) are not flying blind. All alerts go to **Discord**.

## Tool decision (Rule 2 / OCA check)

**Cron probe scripts + Discord**, not Uptime Kuma / Healthchecks.io. Uptime Kuma
is a stateful web app (its own DB + UI) — overkill for "lightweight NOW", and it
would be a new always-on service to secure and back up. A handful of shell
probes on a one-minute cron meet the acceptance (kill Odoo → alert < 2 min) with
zero new runtime surface, and are superseded wholesale by P8-T04. Not an Odoo
module, so no OCA option applies.

## What runs

| Script | Checks | Cron |
|---|---|---|
| `health_check.sh` | Odoo `/web/health` + each tenant subdomain + admin | every minute |
| `resource_check.sh` | disk / memory / 1-min load per core | every 5 min |
| `log_watcher.sh` | Odoo `ERROR`/`CRITICAL` log lines | every 5 min |
| `wal_lag_check.sh` (P2-T04) | WAL-archive lag + archive failures | every 5 min |
| — backup failures (P2-T05) | `ncollection.backup` failure → chatter+activity alert | in Odoo |

`monitor.sh` runs them all in one shot (one cron line). `lib_alert.sh` is the
shared alerter: it **always logs `[ALERT] …`** (journald/cron-mail) and posts to
Discord when `DISCORD_WEBHOOK` is set.

## Setup (server)

```bash
export DISCORD_WEBHOOK=https://discord.com/api/webhooks/...     # in .env / secrets
export MONITOR_HEALTH_URLS="https://admin.ncollectionerp.com/web/health https://<tenant>.ncollectionerp.com/web/health"

# crontab -e (as the deploy user):
* * * * *   cd /opt/ncollection && scripts/monitoring/health_check.sh
*/5 * * * * cd /opt/ncollection && scripts/monitoring/resource_check.sh
*/5 * * * * cd /opt/ncollection && scripts/monitoring/log_watcher.sh
# …or one line:
* * * * *   cd /opt/ncollection && scripts/monitoring/monitor.sh
```

## Thresholds (env)

| Var | Default |
|---|---|
| `MONITOR_DISK_PCT` | 85 |
| `MONITOR_MEM_PCT` | 90 |
| `MONITOR_LOAD_PER_CPU` | 2.0 (1-min loadavg per core) |
| `MONITOR_LOG_SINCE` | 5m |

## Verify the acceptance

```bash
./scripts/monitoring/verify_monitoring.sh
#  [1] healthy → no alert   [2] stop Odoo → alert fires   [3] restart Odoo
```

The one-minute health cron means a killed Odoo alerts within ~1 minute — inside
the 2-minute acceptance bound.

## Boundary

Live probing of real tenant subdomains + the Discord channel is the operator
step (needs the VPS + a webhook). The kill-Odoo → alert mechanism is proven
locally by `verify_monitoring.sh`. Superseded by the P8-T04 observability stack.
