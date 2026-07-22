# Legal & Licensing Notice

Status: Attribution record of record (P1-T13). Applies to the NCollection ERP
product and its white-label presentation.

---

## 1. What NCollection ERP is built on

NCollection ERP is built on **Odoo 19 Community Edition**, licensed under the
**GNU Lesser General Public License version 3 (LGPL-3.0)**, plus a set of
**OCA (Odoo Community Association)** modules, each under LGPL-3.0 or AGPL-3.0
as declared in its own manifest. NCollection's own addons
(`custom_addons/ncollection_*`) are licensed **LGPL-3.0** (see each
`__manifest__.py`).

## 2. Rebranding is permitted — attribution obligations remain

The LGPL permits redistributing and **rebranding the user interface** of the
covered software. Removing the visible "Odoo" name and logo from the running
product (task P1-T13) is therefore lawful. However, the license imposes
obligations that a UI rebrand does **not** discharge:

1. **Preserve license notices in the source.** The copyright headers and
   `LICENSE`/manifest `license` declarations inside Odoo and every OCA module
   MUST remain intact in the distributed source. We do not, and must not,
   strip them. Rebranding is a presentation-layer change only.
2. **Offer the corresponding source (LGPL §4/§5, AGPL §13).** Any recipient of
   the software — and, for AGPL-licensed components accessed over a network,
   any user interacting with them — is entitled to the corresponding source of
   those components, including our modifications to LGPL/AGPL modules. Because
   at least one dependency may be AGPL, the tenant-facing service must be able
   to point users to that source on request.
3. **No misrepresentation of origin.** We may present the product as
   "NCollection ERP" but must not claim to be the author of Odoo or the OCA
   modules, nor apply our copyright to their code.
4. **Modifications to LGPL/AGPL modules stay under the same license.** Our
   overrides that *extend* Odoo/OCA via separate addons are our own work under
   LGPL-3.0; any direct modification of a covered module's own files would
   inherit that module's license (we avoid this — Rule 1: never modify core).

## 3. How this is honored in the codebase

- The white-label work (P1-T13) changes only the **rendered UI** — QWeb
  template inheritance, OWL patches, CSS, and data records inside
  `ncollection_branding`. **No Odoo or OCA source file is edited** (Standing
  Rule 1), so all upstream license headers and notices remain in place.
- Each NCollection addon declares `'license': 'LGPL-3'` in its manifest.
- OCA dependencies are vendored via `repos.yml` / git-aggregator with their
  original `LICENSE` files intact.

## 4. Practical checklist for a release / tenant deployment

- [ ] Upstream `LICENSE` files and manifest `license` keys are unchanged in the
      distributed source.
- [ ] A source-availability path exists for any AGPL component reachable over
      the network by tenant users.
- [ ] Marketing/UI copy presents "NCollection ERP" without claiming authorship
      of Odoo/OCA.
- [ ] Any future direct edit to an upstream file is flagged for license review
      (prefer extension addons — Standing Rule 2).

*This document records attribution obligations; it is not legal advice. For a
commercial launch, have counsel confirm the LGPL/AGPL source-offer mechanics
for the specific dependency set shipped.*

## Changelog

| Date | Change |
|---|---|
| 2026-07-20 | Initial LGPL/AGPL attribution record (P1-T13 white-label completion) |
