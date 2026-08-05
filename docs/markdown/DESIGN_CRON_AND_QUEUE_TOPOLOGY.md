# Design Decision — Outbound Work, Cron Threads and Queue Channels (#310)

**Date:** 2026-08-05 · **Author:** DEV-1 · **Status:** implemented ·
**Scope:** where platform work that can *block on someone else's server* is allowed to run,
and what channel capacities that implies. Follow-up to **#308** (the ECB rate fetch), which
added the platform's first outbound network call.

This record exists because #310's acceptance criteria ask for it in as many words:

> Document the reasoning — a bare number change with no rationale will be re-litigated.

Three of the numbers below look arbitrary and are not. One of them is a number that was
already wrong before this ticket.

## 1. The problem

`config/odoo.prod.conf` sets `max_cron_threads = 1` and `limit_time_real_cron = 3600`.
With one cron thread, `IrCron._process_jobs_loop` runs every ready job **sequentially in that
one thread** (verified in the Odoo 19 source shipped in our image:
`odoo/addons/base/models/ir_cron.py`, jobs selected `ORDER BY failure_count, priority, id`).

So a slow external host doing the ECB fetch *inside* a cron thread blocks **every other
platform cron behind it** — config-sync reconcile and license enforcement among them — for as
long as the fetch lasts, up to `limit_time_real_cron`. In dev it is worse: `config/odoo.conf`
runs `workers = 0`, where Odoo does not enforce `limit_time_real*` at all.

#308 already bounded its own exposure (bounded read, wall-clock deadline, and it *refuses*
rather than falling back to an unbounded read), so this was defence in depth. The structural
risk is that **any future cron doing I/O inherits the problem** — which is why the answer had
to be structural rather than a tweak to the ECB fetch.

## 2. This is not a new rule — it is compliance with an existing one

`ARCHITECTURE_DATA_PLATFORM.md` §"Operational invariants" already says:

> **Cron hygiene** — every `ir.cron` sets sensible batch limits and must be idempotent;
> anything > 30 s of work belongs on the queue runner, not the cron thread.

An outbound HTTP call to a host we do not control is *unbounded-by-nature* work whose whole
failure mode is taking too long. #308's cron was the first thing in the platform to violate
that invariant. #310 does not decide anything new; it brings the code back into line with the
architecture document, which stays authoritative.

## 3. The three findings that shaped the fix

None of these was visible from the issue text, and each would have produced a plausible-looking
change that did not work.

### 3.1 Editing `odoo.prod.conf` would have been a **no-op**

The issue proposes "raise `max_cron_threads`". In the pooling topology cron does not run in the
`odoo` service at all — it runs on **`odoo-bus`**, whose command line carries
`--max-cron-threads=1`. **The CLI flag overrides the conf file.** A change to
`config/odoo.prod.conf` alone would have read correctly, passed lint, passed
`architecture-guard`, passed CI, and changed nothing about the container that actually runs
cron.

This is why the fix edits `docker-compose.pooling.yml`, and why the harness in §5 also passes
`--max-cron-threads=1` on the command line rather than trusting a conf.

### 3.2 Reusing the existing channel would have **relocated** the starvation

The obvious move — enqueue the fetch onto `root.provisioning`, the channel we already have —
just moves the queue. Provisioning, backup, fleet migration and config-sync all share that
channel, so a stalled fetch would sit in a slot **those** jobs need. The tenant waiting for a
workspace would be starved instead of the reconcile cron: a different victim, same bug.

Hence a **dedicated `root.outbound` channel**. It is capacity **1** on purpose — this is the
one place where a slow external host is *allowed* to be slow, in isolation, and one concurrent
outbound job is all the platform needs.

### 3.3 `root` capacity caps the whole subtree — so `root:1` had to change

Confirmed in the OCA source rather than assumed. `queue_job/jobrunner/channels.py`,
`get_jobs_to_run`, pulls a child channel's jobs into the **parent's** queue and then yields
only `while self.has_capacity()`. A child's job therefore has to clear its parent's capacity
too: `root` is a cap on the entire subtree, not a default for jobs sitting directly on `root`.

That has a consequence for the value that was already there:

```
ODOO_QUEUE_JOB_CHANNELS: root:1,root.provisioning:2      # before — self-contradictory
ODOO_QUEUE_JOB_CHANNELS: root:2,root.provisioning:2,root.outbound:1   # after
```

**The old setting was internally inconsistent.** At `root:1` the platform ran exactly ONE
queued job at a time, and the `root.provisioning:2` next to it could never be reached. That
was a pre-existing bug, found while reading the source for this ticket, not something #310
introduced.

At `root:2`, a stalled outbound fetch can hold at most **half** of root, so provisioning /
backup / fleet-migration / config-sync always retain a slot.

### 3.4 Why `--max-cron-threads=2` is *also* in the diff

It is **defence in depth, not the fix.** With outbound work no longer on a cron thread, the
second thread only means that no single long-running cron can serialise every other cron behind
it. If it were the whole fix it would be a bad one: raising the thread count does not stop a
stalled fetch from occupying a thread, it just buys one more thread for the next one to occupy.
That asymmetry is the reason the ticket's "decide: raise the threads, **or** move the work" was
answered with *move the work*, and the thread bump kept only as a backstop.

## 4. What the cron actually does now

```python
@api.model
def _cron_refresh_ecb_rate(self):
    self.with_delay(
        channel=_OUTBOUND_CHANNEL,          # 'root.outbound'
        description="Refresh ECB exchange rate",
        identity_key='nc-ecb-refresh',
    ).refresh_ecb_rate()
```

Two properties are load-bearing and easy to break later:

- **`refresh_ecb_rate` is PUBLIC and must stay so.** `queue_job` persists the method *name*, so
  renaming it breaks any job already enqueued at the time of deploy. `backup.py` documents the
  same constraint for the same reason.
- **`identity_key` is not decoration.** Without it, a runner backlog would stack a day's worth
  of identical fetches against the same host — turning our own retry behaviour into the thing
  the external host sees as abuse.

## 5. How this is verified

Two layers, because they prove different things.

**Unit** — `tests/test_exchange_rate.py::test_the_cron_enqueues_and_does_not_fetch_inline`
proves the *code* no longer fetches on the cron thread. It patches the fetch to raise, so a cron
that still called it fails loudly; asserting merely that `with_delay` was called would have
passed even if the fetch **also** ran inline.

**System** — `make cron-starvation-verify`
(`custom_addons/ncollection_saas/scripts/provisioning/verify_cron_starvation.sh`) proves the
property the issue actually asks about, which no unit test can: that a *genuinely stalled*
outbound fetch does not delay the config-sync reconcile cron.

It runs a real Odoo cron thread with `--max-cron-threads=1` against a container that accepts TCP
on :443 and then never answers, so the TLS handshake hangs and `requests` blocks for its full
read timeout (measured: 30.1 s). Two arms go through identical machinery, and the metric is the
gap between the **two crons starting**, taken from the harness Odoo's own log:

| Arm | `ir.cron` code | Models | Measured reconcile delay |
|---|---|---|---|
| **A** "inline" | `model.refresh_ecb_rate()` | the pre-#310 cron, exactly | **30 s — STARVED** |
| **B** "queued" | `model._cron_refresh_ecb_rate()` | the shipped cron, with a real stalled fetch in flight | **0 s — same tick** |

The 30 s recovered is exactly the stall duration: under the old shape the reconcile waited out
the entire fetch; under the shipped shape it runs in the same cron tick.

Arm A is the point. A single "reconcile ran on time" measurement proves nothing — it would also
pass with spare cron threads, or if the stall never happened, or if the harness measured the
wrong thing. Requiring the two arms to **disagree** is what makes arm B's pass meaningful, so
**arm A failing to starve is a failure of the harness**, not a success.

Wired into `make verify-all` (step 4/6) rather than left as a one-off: #310 is a structural
property that every *future* cron inherits, and an unwired proof is one nobody re-runs.

### 5.1 Two measurements this harness got wrong first — both faking starvation

Recorded rather than quietly fixed, because both produced a **confident green/red that was not
real**, and both are easy to reintroduce.

**The shared database made another Odoo run our crons.** The first harness put its fixture on the
shared dev `db`. But the dev stack's `odoo` container runs with no `-d`, and Odoo's
`cron_database_list()` is `config['db_name'] or list_dbs(True)` — with no `db_name` it returns
**every database on the server** and runs every one's crons. (`--db-filter` does not restrict
this; it only filters HTTP routing.) Verified live: that container was ticking through
`revtest244`, `ci_240`, `saastest`, `e2eadmin`, `t304check` and ~20 other leftover fixtures once
a minute. So the reconcile marker was being set by the *shared* container on its own ~60 s cycle
while the harness's own Odoo never ran a single cron — and the harness reported the resulting
46 s as starvation. Fixed by giving the harness its **own Postgres**, which no other Odoo can
enumerate, and by reading the harness process's **own log** instead of a database row.

**Timing from process start measured the scheduler, not the fix.** Odoo's cron thread blocks in
`select()` for up to `SLEEP_INTERVAL` (60 s) before its *first* poll. Timing from
`"Modules loaded."` therefore added that constant to both arms: arm B read **61 s** for a
reconcile that had in fact run in the same tick as the fetch. Cron-to-cron timing removes the
scheduler's own startup latency, which is not what #310 is about.

The general lesson is the one CLAUDE.md Rule 14 / R-018 already states: a measurement taken
against shared infrastructure is attributable to nobody. Both failures pointed the same way —
toward reporting a problem that did not exist.

Two things the harness deliberately does **not** do: it never edits a bind-mounted source file to
produce the RED arm (arm A is an `ir.cron` row in the harness's own database, and the tree is
mounted read-only), and it redirects `www.ecb.europa.eu` via that one container's `extra_hosts`
rather than a DNS alias on a shared network — an alias would have redirected the live `odoo`
container's outbound traffic too. The whole harness is self-contained: own Postgres, own volumes,
own network. It does not need the dev stack and cannot disturb it.

## 6. What this does *not* cover

- **The queue runner's own starvation is out of scope.** `root.outbound:1` means a second
  outbound job waits for the first. That is intended, and with one daily fetch it is not a
  queue. A second outbound feature should revisit the capacity, not inherit it silently.
- **`limit_time_real_cron` is unchanged** at 3600. With the fetch off the cron thread it is no
  longer the exposure it was; lowering it is a separate call about *all* crons.
- **Dev still runs `workers = 0`** and so still does not enforce cron time limits. The fix
  removes the reason that mattered here; it does not change the dev topology.
- **The harness measures the 30 s idle-timeout stall** (`_RPC_TIMEOUT`). The real worst case is
  larger — `_RPC_DEADLINE` is 60 s total and `config_sync.py` documents ~90 s for deadline plus
  one idle gap. A bigger stall only widens the gap between the two arms.

## 7. What would reopen this

- **A second outbound feature.** `root.outbound:1` was sized for exactly one daily fetch; two
  callers make the capacity a real scheduling decision.
- **Moving off the single-VPS/pooling topology**, where `max_cron_threads` stops being pinned on
  a container command line and §3.1 no longer applies.
- **`queue_job` changing how parent capacity is consumed.** §3.3 is a claim about
  `get_jobs_to_run` in the pinned version; a major bump should re-check it, because the `root:2`
  value is derived from that behaviour and nothing else.
