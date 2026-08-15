# Nginx Reverse Proxy & TLS (ticket P1-T03)

Nginx is the platform's **edge / Layer-1 security front door**
(`docs/markdown/ARCHITECTURE_SECURITY.md`). It terminates TLS, routes each
tenant subdomain to Odoo, adds security headers, throttles login, blocks the DB
manager, and proxies the realtime websocket bus to the correct Odoo port.

It lives in the **compose overrides** — the base `docker-compose.yml` stays
`odoo + db` only (so `docker compose up` and the CI `build` job keep working
unchanged). Nginx is layered in for dev and prod:

| Environment | File | Ports | Domain | TLS | Bus → |
|---|---|---|---|---|---|
| **Dev** | `conf.d/ncollection.dev.conf` | 80 | `*.localhost` | none | Odoo **8069** |
| **Prod** | `conf.d/ncollection.prod.conf` | 80 → 443 | `*.ncollectionerp.com` | Let's Encrypt wildcard | Odoo **8072** |

```
nginx/
├── conf.d/
│   ├── ncollection.dev.conf     # *.localhost, HTTP, ws→8069
│   └── ncollection.prod.conf    # *.ncollectionerp.com, TLS+HSTS, ws→8072
├── snippets/
│   ├── proxy.conf               # Host + X-Forwarded-* (feeds Odoo proxy_mode), ws upgrade, timeouts
│   ├── security-headers.conf    # X-Frame-Options, nosniff, Referrer-Policy, CSP
│   └── gzip.conf                # gzip for static types only (BREACH-safe)
└── README.md
```

---

## ⚠️ The websocket port trap (read this once)

Odoo serves its realtime bus on a **different port depending on run mode**:

- **Dev** (`workers=0`, threaded) → bus on **8069** (same as HTTP).
- **Prod** (`workers=4`, gevent) → bus on a dedicated gevent worker, **8072**.

Route `/websocket` to the wrong port and the realtime bus dies **silently** —
no error page, just no live chat / notifications / presence. That is exactly
why dev and prod are **two separate config files**, each pointing the
`odoo_bus` upstream at the port that mode actually uses. After any change,
verify the bus (see "Verify" below) — do not assume.

---

## Dev usage

Nginx is **on by default** for local work via the Makefile (which layers in
`docker-compose.dev.yml`):

```bash
make up          # starts db + odoo + nginx(:80) + pgAdmin
```

or explicitly:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

Then reach Odoo through the edge. Browsers resolve `*.localhost` to
`127.0.0.1` automatically; if yours does not, add hosts entries or use curl's
`Host` header:

```bash
curl -I -H "Host: ncollection.localhost" http://127.0.0.1/web/login
```

Dev is plain HTTP by design — no certs, no HSTS. Odoo on `:8069` also stays
directly reachable for debugging.

## Prod usage

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Prod nginx needs the **wildcard cert in place before it boots** (nginx refuses
to start without the `ssl_certificate` files). Issue it first ↓.

---

## 🔐 Issuing the wildcard certificate

A wildcard (`*.ncollectionerp.com`) **must** use the **DNS-01** challenge —
HTTP-01 cannot validate wildcards. DNS-01 proves domain control by creating a
`_acme-challenge` TXT record, so you need your DNS provider's API.

Set the domain/email in `.env` (see `.env.example`: `DOMAIN`, `CERTBOT_EMAIL`).

### Option A — automated DNS-01 (recommended, unattended renewal)

Use the Certbot image for **your** DNS provider (Cloudflare shown; Route 53,
Google, etc. have their own plugins) and give it a scoped API token.

```bash
# 1. Put your provider API token in a gitignored creds file, e.g.:
#    echo "dns_cloudflare_api_token = <token>" > secrets/cloudflare.ini
#    chmod 600 secrets/cloudflare.ini
# 2. Issue the wildcard (+ apex) cert into the shared certbot volume:
docker run --rm \
  -v ncollection-erp_certbot_certs:/etc/letsencrypt \
  -v "$PWD/secrets/cloudflare.ini:/creds.ini:ro" \
  certbot/dns-cloudflare certonly \
    --dns-cloudflare --dns-cloudflare-credentials /creds.ini \
    -d "ncollectionerp.com" -d "*.ncollectionerp.com" \
    --email "$CERTBOT_EMAIL" --agree-tos --no-eff-email
```

For unattended renewal, point the `certbot` service in
`docker-compose.prod.yml` at the same `certbot/dns-<provider>` image and mount
the creds file; its loop runs `certbot renew` twice daily.

### Option B — manual fallback (no DNS API / one-off)

When you cannot wire a DNS plugin, issue interactively — Certbot prints the TXT
record to add by hand:

```bash
docker run --rm -it \
  -v ncollection-erp_certbot_certs:/etc/letsencrypt \
  certbot/certbot certonly \
    --manual --preferred-challenges dns \
    -d "ncollectionerp.com" -d "*.ncollectionerp.com" \
    --email "$CERTBOT_EMAIL" --agree-tos --no-eff-email
# Certbot pauses and shows a TXT value; add it at your DNS host, wait for
# propagation (dig _acme-challenge.ncollectionerp.com TXT), then press Enter.
```

> Manual certs **cannot auto-renew** — repeat this ~every 60 days, or switch to
> Option A before go-live.

After either option, `docker compose ... -f docker-compose.prod.yml restart nginx`
so nginx picks up the new cert.

> **Volume name:** commands above use `ncollection-erp_certbot_certs` (Compose
> prefixes the volume with the project name = repo folder). Confirm with
> `docker volume ls | grep certbot`.

---

## What the edge enforces (acceptance criteria)

- **Subdomain → tenant DB.** `Host` + `X-Forwarded-*` are forwarded so Odoo's
  `proxy_mode=True` + `db_filter=^%d$` pick the DB from the subdomain.
- **Security headers** on every response: `X-Frame-Options`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Content-Security-Policy`,
  and (prod/HTTPS only) `Strict-Transport-Security`.
- **DB manager blocked:** `/web/database/*` → `403` at the edge.
- **Login throttled:** `limit_req` on `/web/login` (returns `429` when tripped).
- **API token endpoint throttled (#436):** `limit_req zone=apitoken` on
  `/api/v1/oauth/token` (`429` when tripped). Until #436 this path matched **no
  location at all** and fell through to `location /`, so the public API's
  failed-authentication path had no edge limit — and every invalid attempt costs
  a PBKDF2 in Odoo. This zone rejects before Odoo is reached, so the hash never
  runs. `ncollection.api.throttle` is the independent app-layer second half; the
  two are not redundant, because the app counter is per-database (an attacker
  rotating tenant subdomains gets a fresh bucket) while this zone is keyed on
  source IP globally. **Exact match, deliberately:** the business endpoints
  P8-T02 (#78) adds must be sized on their own traffic, not by a number chosen
  for a credential endpoint.
- **Realtime bus** proxied to the mode-correct Odoo port (8069 dev / 8072 prod).
- **TLS** (prod): TLS 1.2/1.3, wildcard cert, HTTP→HTTPS 301, HSTS.

## CSP note

The CSP in `snippets/security-headers.conf` is deliberately **Odoo-compatible**
(`'unsafe-inline'`/`'unsafe-eval'` are required by Odoo's OWL/QWeb web client).
Tightening it (nonces, dropping `unsafe-*`) is a later hardening pass — do not
tighten blindly or the backend white-screens.

## Custom tenant domains (P2-T06)

Platform **subdomains** (`<db>.ncollectionerp.com`) need **zero** work here —
they ride the wildcard block above and the `*.ncollectionerp.com` cert. The
platform just tracks them (`ncollection.domain`, auto-created on provisioning)
and a weekly cron alerts 14 days before the cert expires.

A tenant's **own** domain (e.g. `erp.acme.com`) is onboarded per domain:

```bash
# 1. Issue a per-domain cert (HTTP-01; the :80 block serves the challenge):
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec certbot \
  certbot certonly --webroot -w /var/www/certbot -d erp.acme.com
# 2. Render the server block from the Jinja2 template into the include dir:
sed 's/{{ fqdn }}/erp.acme.com/g' nginx/templates/tenant-custom-domain.conf.j2 \
  > nginx/conf.d/tenants/erp.acme.com.conf
# 3. Validate + graceful reload:
./scripts/deploy/nginx-reload.sh
```

> **Deferred live-wiring:** activating `conf.d/tenants/` requires mounting that
> dir into the nginx container (a prod-compose change) — intentionally not done
> yet, since no custom domain exists. The template + reload script + tracking
> model ship now as the scaffolding; the compose mount + first real custom
> domain are a follow-up. Odoo never reloads nginx itself (that needs the Docker
> socket, restricted by **P2-T08**) — the reload stays host-side.

## OCA / reuse decision (Standing Rule 5)

Nginx is **infrastructure**, not an Odoo module — there is no OCA addon to
reuse here. The config follows Odoo's own reference reverse-proxy guidance
(upstreams, `proxy_mode` headers, the 8072 longpolling/websocket split). The
app-layer brute-force lockout is the OCA `auth_brute_force` module, scoped
separately to **P1-T19**. Domain/SSL **automation** (P2-T06) is likewise
infra-specific to this nginx+certbot topology — no OCA module manages external
server blocks; the `ncollection.domain` tracking model is custom, by design.
