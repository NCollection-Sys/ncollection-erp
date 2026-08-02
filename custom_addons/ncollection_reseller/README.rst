===================================
NCollection White-Label Reseller System
===================================

Platform (admin-DB) feature (P10-T09) that lets partner accounts resell
NCollection under their own brand.

Features
========

* **Reseller record** linked to a ``res.partner``, with brand defaults,
  sub-tenant quota, and revenue-share percentage.
* **Sub-tenant link**: a nullable ``reseller_id`` on ``ncollection.tenant``;
  the standard (non-reseller) checkout flow is unchanged.
* **Cascading branding**: a reseller's brand is pushed to each sub-tenant
  through the sanctioned ``config-sync`` RPC channel (never a cross-DB read)
  and applied to the tenant's ``res.company`` *override-if-default*, so a
  tenant that customises its own theme is never clobbered.
* **Provisioning quota** enforced at the ORM ``create`` layer (every creation
  path is protected, not just the wizard).
* **Partner dashboard**: a reseller group scoped by record rules to its own
  sub-tenants, a provisioning wizard, and a revenue-share report.

Architecture
============

All reseller state lives in the admin (platform) database. Brand values reach
tenant databases only through ``ncollection_saas``'s per-tenant-keyed
config-sync bridge — the two-layer separation is preserved and no Odoo core
files are modified.

Known limitation
================

Branding uses an *override-if-default* heuristic rather than an explicit
platform-managed / tenant-managed provenance model. Introducing explicit
branding ownership is deferred to the #102-era reseller branding work.
