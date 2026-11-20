# Custom tenant domains

A tenant can be reached on its **platform subdomain** (`acme.balancecloud.ae`, `acme.atlas-core.io`) and, optionally,
on a **verified custom domain** the customer owns (`erp.acme-co.com`). Same feature, same code on every platform;
everything platform-specific is configuration. Changing a tenant's address never re-creates, renames or
re-provisions its database.

## Who does what

| Layer | Component | Responsibility |
|---|---|---|
| Platform DB | `ncollection.domain` (`ncollection_saas/models/domain.py`) | desired state, every authorisation decision, DNS verification, status |
| Platform DB | Tenant form > **Domains** tab (`tenant_domain.py`, `domain_views.xml`) | Option A (subdomain) / Option B (custom domain), primary address, redirect policy |
| Platform DB | `/ncollection/edge/v1/state` + `/report` (`controllers/edge.py`) | the edge agent's only door; HMAC bearer, 404 on a wrong key |
| Host | `scripts/edge/nc_edge_agent.py` (systemd timer, root) | second ownership check, certificate (Let's Encrypt HTTP-01), nginx block, HTTPS proof, report |
| Tenant DB | `ncollection.admin.service.set_canonical_host` (`ncollection_core`) | `web.base.url` (links in mail, resets, invitations), allowed aliases, optional redirect |
| Every server | `ncollection_core/tenant_host_pin.py` (server-wide) | a verified custom domain reaches exactly ONE database |

Rule 3 holds: the platform never touches a tenant database; it calls the tenant's admin service over the existing
tenant-administration channel (derived bearer + the tenant's `platform_admin_ids` allow-list).

## Lifecycle

```
Add (Domains tab)          -> Pending Verification     a random 32-byte token; DNS instructions shown
Verify (TXT + A/AAAA/CNAME) -> DNS Configuration Required   token proven but the name does not (only) reach this edge
                            -> SSL Pending              proven AND resolves only to the edge
edge agent: re-check DNS, ACME, nginx -t, reload, https://<domain>/web/health = 200
                            -> Active                   token deleted; certificate expiry tracked
Make Primary Address        -> tenant told first (web.base.url, aliases, redirect); on refusal nothing changes
Disable                     -> edge removes the block next run; primary falls back to the subdomain
Enable Again                -> Pending Verification with a NEW token (ownership proven from scratch)
Remove (only when Disabled) -> the record goes; the tenant, its database and data are untouched
```

`Failed` = the edge gave up (5 attempts with back-off 1 h / 6 h / 24 h — Let's Encrypt rate limits); Verify again.
A domain is never Active before the edge has served HTTPS for it.

## DNS instructions shown to the administrator

| Record | Name | Value | When |
|---|---|---|---|
| TXT | `_nc-verify.<domain>` | `nc-verify=<token>` | always (proof of control; may be removed once Active) |
| CNAME | `<domain>` | the platform's CNAME target | a subdomain such as `erp.example.com` |
| A / AAAA | `<domain>` | the platform's edge IPv4 / IPv6 | an apex (`example.com`): a CNAME is invalid there; ALIAS/ANAME works where the provider offers it |

A record that points anywhere else (a stray AAAA from the previous host) blocks activation: it would break
issuance and send some visitors elsewhere.

## Security controls (acceptance, Part 6)

| Threat | Control | Test |
|---|---|---|
| Claim someone else's domain | TXT token per domain (platform) + re-checked by the edge on two public resolvers | `test_verification_needs_the_token...`, agent `test_missing_ownership_token_issues_nothing` |
| Duplicate / cross-tenant | `unique(fqdn)` incl. archived rows | `test_platform_namespace_duplicates...` |
| Cross-platform collision | edge claim register: a hostname belongs to the first platform that claimed it; two at once = both refused | agent `test_same_hostname_on_two_platforms...`, `test_first_claim_wins...` |
| Hijack a neighbouring site / platform namespace | edge refuses any name another nginx file on the host serves (`.balancecloud.ae`, `fresh-origin.com`, ...); platform refuses its own namespace | agent `test_a_neighbouring_site...` |
| Unverified host reaches a tenant | only verified domains get a block; the db is pinned by the block, not by the Host | `test_edge_sees_only_verified...` |
| Host-header / database spoofing | `X-NC-Tenant-DB` cleared in every other block (`nginx/snippets/proxy.conf`), honoured only with `NC_TENANT_DB_HEADER=1`, pinned = the ONLY database (session cookie and `X-Odoo-Database` cannot widen it), malformed = no database | `TestTenantHostPin` |
| Tenant admin bypass | every action and field is ORM-guarded: platform admin + allow-list; status/token/primary/fqdn/tenant not writable | `test_only_an_allow_listed...`, `test_status_token_primary...` |
| Redirect to an attacker | the tenant redirects only FROM a known alias TO the canonical host, both set by the platform after verification; never for RPC/JSON-2/websocket/health/assets/POST | `TestCanonicalHost` |
| Deactivation / removal | disabled = block removed; removing never touches the tenant | `test_disabling_the_primary...`, agent `test_disabled_domain_is_retired...` |
| Outage flapping | a platform that does not answer keeps its blocks | agent `..._unreachable_platform_keeps_its_domains` |
| Token retention | cleared when Active; a re-enable issues a new one | `test_edge_sees_only_verified...`, `test_re_enabling...` |

## Per-platform configuration (nothing is hardcoded)

Management > Settings > **Custom Tenant Domains**: enable, edge IPv4, edge IPv6, CNAME target (parameters
`ncollection_saas.custom_domains_enabled|custom_domain_ipv4|custom_domain_ipv6|custom_domain_cname`). The base
domain stays `ncollection_saas.base_domain` / `NC_BASE_DOMAIN`.

Server (per platform): `NC_TENANT_DB_HEADER=1` in the odoo service environment, set ONLY after the platform's proxy
snippet clears `X-NC-Tenant-DB` (order matters: snippet first, then the variable).

Host (one agent for all platforms): `/etc/nc-edge/edge.json`:

```json
{"platforms": [
  {"name": "balancecloud", "database": "balancecloud", "host": "balancecloud.ae", "env_file": "/opt/balancecloud/.env",
   "internal_url": "http://127.0.0.1:8069", "upstream": "127.0.0.1:8069", "bus": "127.0.0.1:8072",
   "snippet_dir": "/etc/nginx/balancecloud/snippets", "zone_prefix": "bc_"},
  {"name": "atlascore", "database": "atlas-core", "host": "atlas-core.io", "env_file": "/opt/atlascore/.env",
   "internal_url": "http://127.0.0.1:8169", "upstream": "127.0.0.1:8169", "bus": "127.0.0.1:8172",
   "snippet_dir": "/etc/nginx/atlascore/snippets", "zone_prefix": "ac_"}],
 "nginx_dir": "/etc/nginx/sites-enabled", "state_file": "/var/lib/nc-edge/state.json",
 "cert_dir": "/etc/nc-edge/lego", "webroot": "/var/www/nc-edge-acme", "acme_email": "<ops mailbox>",
 "lego_cmd": "docker run --rm -v /etc/nc-edge/lego:/etc/nc-edge/lego -v /var/www/nc-edge-acme:/var/www/nc-edge-acme goacme/lego:v4.29.0@sha256:baafd8174bd703fb124cfae562741193ad7041bd6f8dec3d98f64cb51e939d04",
 "edge": {"ipv4": "<edge v4>", "ipv6": "<edge v6>"}, "resolvers": ["1.1.1.1", "8.8.8.8"]}
```

The bearer is derived (`HMAC-SHA256(NC_CONFIG_SYNC_KEY, "nc-edge-v1:" || platform db)`) from each platform's
root-only `.env`; nothing is stored. Generated files: `nc-domain-<fqdn>.conf` (one per domain) and
`nc-edge-common.conf` (the websocket `map`). Never edit them; the agent rewrites them.

## Tests

* `ncollection_saas/tests/test_custom_domain.py` (platform), `ncollection_core/tests/test_custom_domain_tenant.py`
  (pin, canonical host, redirect).
* `scripts/edge/test_nc_edge_agent.py`: the agent against fake nginx / DNS / CA / curl and two fake platforms
  (`python3 scripts/edge/test_nc_edge_agent.py`).
* What these do NOT prove: a real public DNS record and a real certificate for a customer domain. That needs a
  hostname in a zone outside both platform namespaces; see the release record for whether it was run.
