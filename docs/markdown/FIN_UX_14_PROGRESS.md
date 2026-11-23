# Financial UX — 14 requests: progress log (local only, not pushed)

Branch `feature/fin-ux-14` in worktree `D:\Projects\ncwt-acctset`, base `403b07a`.
Dev runtime: container `nc_fx14_web` (127.0.0.1:8996, addons = this worktree), DBs `fx14a` (UAE, Awtar), `fx14b`
(UAE + branch + unrelated USD company), `fx14c` (Egypt on the generic chart), `fx14n` (copy of the local `ncollection`, real
bank data), filestore volume `nc_fx14_fs`. Created 2026-11-23 from `acctbrw*` / `ncollection`. Never push or deploy.

Local runtime synced to 403b07a on 2026-11-23 00:18 UTC: branch `release-403b07a` in D:\Projects\ncollection-erp; ncollection /
awtar / ncollection_demo `-u ncollection_core,ncollection_account_core`, rc=0, counts identical (backup
`D:\Backups\local-sync-403b07a-20261123T001339Z`).

| # | Request | Status | Commit |
|---|---------|--------|--------|
| 08 | Bank transactions running balance (P0) | fixed: confirmation folded into its payment, value-date order, journal currency | ba1a266 |
| 16 | Account Balances audit | fixed: Opening/Debit/Credit/Closing, drill = ledger viewer | (account_reports) |
| 09.1 | Clickable Current Balance | done | b390f52 |
| 12 | Five card buttons | removed, moved to card menu | b390f52 |

Root causes recorded:
- 08: a Paid receipt kept liquidity 0 on its row; its bank line was a separate muted "Bank Confirmation" row (sometimes
  dated BEFORE the payment, e.g. fx14n payment 39). Running balance was in company currency while amounts were in
  payment currency.
- 16: "Balance" was the period movement with no opening; the drill opened journal items whose `balance` column is per
  line. Liquidity Transfer (fx14n account 226) nets to 0 over its 10 posted items, correctly.
