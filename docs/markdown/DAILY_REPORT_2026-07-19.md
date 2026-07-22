# What We Built Today — Explained Super Simply

**Day:** 19 July 2026
**For:** Omar (DEV-1)
**Promise:** every big word explained like you are 5 — but nothing skipped.

---

## The words we need today (30 seconds)

| Word | Baby words |
|---|---|
| **The bouncer (Nginx)** | A guard standing at the store's front door. Checks tickets, sends people to the right room, kicks out troublemakers. |
| **db_filter / routing** | The rule "your door name = your room". `clienta.something` can ONLY enter room `clienta`. |
| **Session cookie** | The "I'm already inside" sticker you get after login. |
| **OCA** | A big library of **free, community-built LEGO pieces** for Odoo. We borrow instead of building. |
| **Pin / pinning** | Writing down the EXACT page of the LEGO instruction book we used, so everyone builds the identical thing forever. |
| **CI robots** | The 4 robot checkers that inspect every piece before it joins the castle. |

Today we finished **5 jobs + 1 tool**. Here they are. 🧱

---

## Build 1 — The bouncer at the front door 🚪 (Job #4 = P1-T03, PR #135)

Before today, anyone could walk straight to the store's back door (`localhost:8069`).
Now a **bouncer (Nginx)** stands in front:

- **Checks and forwards the door name** so the store knows WHICH shop you want.
- **Slams the "list all shops" door** — `/web/database/…` answers a hard **403 NO**.
- **Counts login knocks** — hammer the login door and you get told to wait (rate limit).
- **Wears the security badges** (headers that stop common browser attacks).
- Has a **practice outfit** (dev, plain HTTP) and a **match-day outfit** (prod, TLS/HTTPS + certificates, ready for the real domain).

**The scary trap we dodged (and PROVED):** Odoo's live-chat wire lives on port **8069** in practice mode but port **8072** on match day. Plug the wire into the wrong hole and chat dies **silently** — no error, just silence. We wired each outfit to its correct hole and read the bouncer's own logbook to prove the wire landed on 8069. ✅

Bonus fix on the way: pgAdmin was crash-looping because it hates `.local` email addresses. One line, fixed.

---

## Build 2 — A scoreboard that survives ☠️→📋 (PR #136)

**Your worry (a smart one):** "Only GitHub knows which jobs are done. If GitHub breaks — we know nothing."

**What we built:** a command that asks GitHub about all 100 jobs and writes the answers into a file **inside the repo**: `PROGRESS.md`. A scoreboard per phase + one row per task. Re-run it any time:

```bash
python scripts/github_issue_sync.py --report
```

It even caught a mistake on its first run: job P1-T01 had **two** tickets (#2 and #102). The robot noticed; humans hadn't.

---

## Build 3 — Proof that shops can't peek at each other 🕵️ (Job #7 = P1-T06, PR #137)

This is the platform's **backbone**: `clienta.localhost` must reach ONLY the `clienta` room.

We built three practice rooms (`clienta`, `clientb`, `admin`), each with a name-tag inside, plus a **robot inspector** (`make routing-verify`) that checks **8 things**:

1–2. Each door leads to its OWN room (we read the name-tag to be sure). ✅
3. Asking door A for room B → **refused**. ✅
4–5. An "I'm inside" sticker from shop A is **worthless** at shop B — tested BOTH directions. ✅
6–8. The "list all shops" doors → **403, 403, 403**. ✅

**8 out of 8. Twice.** And it's a saved script — anyone can re-run the proof any day. The report card lives in `docs/ROUTING.md` with a picture of how a request travels.

---

## Build 4 — Hide the word "odoo" from visitors 🎭 (Job #16 = P1-T15, PR #138)

White-labeling: customers should see OUR brand, not the engine's name.

We probed every public door and found exactly **one** leak: visiting the naked address bounced you to `/odoo` in the URL bar. The bouncer now quietly rewrites that one signpost — visitors see clean addresses only. We also added short vanity doors: `/login`, `/signup`, `/reset`, `/portal`.

**The line we refuse to cross (on purpose):** INSIDE the store, the shelves are labeled `/odoo/...` by Odoo's own machinery. Repainting those breaks the store on every upgrade. Inside stays as-is — documented so nobody "fixes" it later.

A mid-build discovery made it BETTER: sending visitors to `/web` instead of `/web/login` means logged-in people land straight at their desk with zero extra hops.

---

## Build 5 — The LEGO library gets a catalog 📚 (Job #5 = P1-T04, PR #139)

**What we found when we opened the cupboard (surprise!):** a teammate had photocopied **five entire LEGO instruction books — 2,381 pages — straight into our repo**, with no note of which edition. Worse: **none of it was plugged in.** Odoo couldn't see a single page, and one of our own modules (`ncollection_mis_templates`) was **impossible to install** because of it.

**What we did:**
- Wrote a tiny **catalog card** (`repos.yml`): five books, each pinned to an EXACT edition (commit hash).
- **Threw out the 2,381 photocopies.** One command (`make oca`) re-fetches the exact editions onto any machine.
- **Plugged the shelf in** (mounted + addons_path) — and proved it: the broken module now **installs, with its whole chain**.
- Taught the CI robots to fetch the same pinned editions and test them on every PR.
- Proof of sameness: a pretend "new laptop" clone rebuilt the library **bit-for-bit identical — zero differences**.

Updating a book later = change one hash → PR → robots check → merge. Documented in `OCA_DEPENDENCIES.md`.

---

## Build 6 — Locks, alarms, and a guest book 🔐 (Job #20 = P1-T19, PR #141 — **awaiting your merge**)

Three protections, in one new room (`ncollection_auth`) installed in EVERY database:

1. **A guest book** (`ncollection.auth.log`): every login, failed login, logout, and password reset gets written down — who, from which IP, which browser, which database. It only *watches*; it never decides who gets in. Clever bit: failed logins are written with a **separate pen** so the "you're rejected!" eraser can't erase the note.
2. **A login lock**: 5 wrong passwords → locked out for 5 minutes. Plot twist: the community's lock (`auth_brute_force`) turned out to be **abandoned years ago — it doesn't exist for modern Odoo at all**. But digging in Odoo 19's own engine we found a **built-in lock** already there — we just turned the key (two settings). Zero custom security code = the safest kind.
3. **A sleep timer**: idle for 2 hours → logged out automatically (community module `auth_session_timeout`, which DOES exist for 19, pinned in our new catalog).

Only Settings-level users may read the guest book; **nobody** may edit it. Mirrored at the deep (ORM) layer, not just the menus.

**Why this one took three robot rounds (honesty corner):** the robots rejected us twice, and each rejection taught us something real — (a) two of the lock's settings already exist in a fresh database so you must *update*, not *create* them; (b) Odoo checks a password against **the user asking**, not the user named — our test asked as the wrong person; (c) the test playground deliberately neutralizes the exact "separate pen" trick we were testing, so the test now watches the pen get picked up instead. All three root-caused from Odoo's source, not patched blindly. Round three: **all 4 robots green.**

---

## Where everything stands ✅

| Thing | Status |
|---|---|
| Jobs finished & merged today | **5**: P1-T03 (#4), P1-T06 (#7), P1-T15 (#16), P1-T04 (#5) + the progress tracker |
| Awaiting your merge | **PR #141** (P1-T19) — all 4 robots green |
| Scoreboard | **8/100 done · Phase 1: 8/21 (38%)** → 9/100 and 43% once #141 lands |
| Teammate (DEV-2) today | merged their T07 + T08 (tenant models + roles) — no collisions with us all day |
| Your dev stack | running & healthy; nginx edge on :80 is now the default `make up` |
| One-time step for every laptop (after pulling) | `make oca` (fetches the pinned LEGO books) |

## Rocks still on the road 🪨 (nothing urgent)

1. **Three evidence items** promised before closing #20: live cookie-flag check, reset-token double-use test, and the two-lock login hammer. Listed in PR #141.
2. **Duplicate ticket** P1-T01 (#2 + #102) — one should be marked duplicate someday.
3. **The prod master-key placeholder** (from P1-T02) still needs a real secret before go-live.
4. A leftover practice DB `authtest` may sit on your machine — `make dropdb db=authtest` wipes it.

## What's next 👉

After #141 merges: **P1-T05 (CI pipeline enhancement, #6)** is unblocked (T01 ✅ + T04 ✅) — the natural next DEV-1 move. Then the DEV-2-dependent chain (T20 isolation suite, T21 audit) starts lining up.

*That's the whole day — six builds, five merged, one on your desk. 🔥*
