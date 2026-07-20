<!--
Keep this short. Every prompt below exists because its absence let a real
regression through — see docs/markdown/REGRESSIONS.md. Delete sections that
genuinely do not apply (docs-only PRs rarely need a blast radius).
-->

## What & why

<!-- The change, and the problem it solves. Link the issue: Closes #<n> -->

## Blast radius

<!--
Which ALREADY-SHIPPED work could this touch? Scripts, compose files, nginx,
workflows, fixture databases, shared addons. "None — new files only" is a fine
answer when true. Cross-suite breakage stayed invisible for weeks precisely
because nobody was asked this.
-->

| Artifact touched | Owning ticket | Risk |
|---|---|---|
| | | |

## Verification

<!--
Paste real output, not intentions. `make verify-all` runs routing +
provisioning + e2e — run it, not just the suite for your own lane.
-->

| Check | Result |
|---|---|
| `make verify-all` | |
| CI | |

## What this does NOT cover

<!--
Name the gaps honestly. An undeclared gap reads as coverage and is how a
"passing" suite hides a regression.
-->

## Rollback

<!-- How to undo this cleanly if it misbehaves after merge. -->

---

### Checklist

- [ ] Ran `make verify-all` (not just my own lane's suite)
- [ ] Gates pass locally: `flake8` · `shellcheck` · `invariants.py` · `architecture_guard.py`
      (or `make hooks-install` once, and let pre-push run them)
- [ ] Any new guard/rule traces to a **specific** bug, not generic best practice
- [ ] If this fixes a regression: added an entry to `docs/markdown/REGRESSIONS.md`
- [ ] Touched files **outside `custom_addons/`**? Then `architecture-guard` did not review
      them architecturally — say so, and ask for manual review
- [ ] No secrets; dev credentials stay in `.env`

> ⚠️ **CI cannot block this merge.** Branch protection is unavailable on GitHub Free
> private repos (verified: HTTP 403), so a red PR is merge-able. The post-merge canary
> will file a `broken-develop` issue if develop breaks — that is detection, not a gate.
> See `docs/markdown/BRANCH_PROTECTION.md`.
