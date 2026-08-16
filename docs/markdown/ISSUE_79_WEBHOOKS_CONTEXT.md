# Issue #79: [P8-T03] Webhooks System — Architectural Context & Implementation Blueprint

---

## 1. Issue Overview
- **Issue**: [#79 [P8-T03] Webhooks System](https://github.com/NCollection-Sys/ncollection-erp/issues/79)
- **Role**: DEV-1 (Platform & API Services)
- **Module**: `custom_addons/ncollection_api/`
- **Dependencies**: #78 (`[P8-T02] REST Business Endpoints` - Merged in PR #442)

---

## 2. Technical Requirements

### A. Data Models
1. **`ncollection.webhook.subscription`**:
   - `name`: Char, display name
   - `client_id`: Many2one `ncollection.api.client` (optional link to OAuth2 client)
   - `url`: Char, target HTTPS endpoint URL (guarded against localhost/internal SSRF)
   - `secret`: Char, signing secret for HMAC-SHA256
   - `active`: Boolean, default True
   - `event_ids`: Many2many `ncollection.webhook.event` or Selection of supported events:
     - `sale.order.confirmed`
     - `account.move.posted`
     - `account.payment.received`
     - `crm.lead.created`
     - `stock.level.low`
   - `failure_count`: Integer, consecutive delivery failures
   - `state`: Selection (`active`, `degraded`, `disabled`)

2. **`ncollection.webhook.delivery`**:
   - `subscription_id`: Many2one `ncollection.webhook.subscription`, required, ondelete='cascade'
   - `event`: Char, event identifier
   - `payload`: Text / JSON, event payload
   - `response_code`: Integer, HTTP response status code
   - `response_body`: Text, response snippet (truncated to 1024 bytes)
   - `duration_ms`: Float, request latency in milliseconds
   - `attempt`: Integer, attempt counter (1 to 5)
   - `next_retry`: Datetime, next scheduled delivery attempt
   - `state`: Selection (`pending`, `delivered`, `failed`, `dead_letter`)
   - `error_message`: Text, connection error / timeout details

### B. Security & Cryptographic Signing
- Header: `X-NCollection-Signature: sha256=<hex_digest>`
- Header: `X-NCollection-Timestamp: <epoch_timestamp>`
- Header: `X-NCollection-Event: <event_name>`
- Header: `X-NCollection-Delivery: <delivery_uuid>`
- Signature Formula: `HMAC-SHA256(secret, timestamp + "." + json_payload)`

### C. Delivery Engine & Retry Topology
- Non-blocking delivery via queue runner or background cron (`_nc_process_webhook_queue`)
- Exponential backoff schedule:
  - Attempt 1: Immediate
  - Attempt 2: +60 seconds
  - Attempt 3: +300 seconds (5 min)
  - Attempt 4: +1800 seconds (30 min)
  - Attempt 5: +7200 seconds (2 hours)
  - Max Retries Exceeded: Transition to `dead_letter`

### D. ORM Event Emitters
- `sale.order`: Hook in `action_confirm` to emit `sale.order.confirmed`.
- `account.move`: Hook in `action_post` to emit `account.move.posted`.
- `crm.lead`: Hook in `create` to emit `crm.lead.created`.

---

## 3. Test & Verification Plan
1. **`test_webhook_subscription_crud`**: Create, edit, and disable webhook subscriptions.
2. **`test_webhook_signature_calculation`**: Verify HMAC-SHA256 signature matches expected digest.
3. **`test_webhook_successful_dispatch`**: Mock HTTP receiver returning 200 OK transitions delivery to `delivered`.
4. **`test_webhook_retry_exponential_backoff`**: Flaky receiver returning 500 triggers retry calculation with backoff.
5. **`test_webhook_dead_letter_ceiling`**: 5 consecutive failures mark delivery as `dead_letter` and degrade subscription.
6. **`test_webhook_ssrf_protection`**: Validate URL guard rejects loopback / link-local addresses.
