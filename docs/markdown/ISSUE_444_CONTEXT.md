# Issue #444: [P8-T10] Uniform Error Handling & Structured Incident Logging — Context Blueprint

---

## 1. Issue Overview
- **Issue**: [#444 [P8-T10] Uniform Error Handling & Structured Incident Logging](https://github.com/NCollection-Sys/ncollection-erp/issues/444)
- **Phase**: 8 (Platform Services)
- **Role**: DEV-1
- **Modules**: `custom_addons/ncollection_branding/`, `custom_addons/ncollection_core/`
- **Dependencies**: None (self-contained)

---

## 2. Technical Architecture & Component Boundaries

### Component A: `ncollection_core` — Structured Error Model & Sanitizer
1. **Model: `ncollection.error.log`** (`custom_addons/ncollection_core/models/error_log.py`):
   - `uuid`: `fields.Char(default=lambda self: self._nc_generate_error_id(), readonly=True, index=True)` (format: `ERR-XXXXXX-XXXX`)
   - `error_type`: `fields.Char(string="Error Type", index=True)`
   - `message`: `fields.Text(string="Error Summary")`
   - `http_status`: `fields.Integer(string="HTTP Status Code", index=True)`
   - `route`: `fields.Char(string="Route / Endpoint", index=True)`
   - `method`: `fields.Char(string="HTTP Method")`
   - `user_id`: `fields.Many2one('res.users', string="User", ondelete='set null')`
   - `tenant_id`: `fields.Many2one('ncollection.tenant', string="Tenant", ondelete='set null')`
   - `traceback_masked`: `fields.Text(string="Masked Traceback")`
   - `remote_addr`: `fields.Char(string="Client IP")`
   - `user_agent`: `fields.Char(string="User Agent")`
2. **Sanitizer Engine**:
   - `_nc_sanitize_traceback(tb_str)`: Uses regex replacement to mask passwords (`password='...'` -> `password='***'`), bearer tokens (`Bearer ...` -> `Bearer [MASKED]`), database DSNs, and credit card / PAN patterns.
3. **Security ACLs**:
   - `custom_addons/ncollection_core/security/ir.model.access.csv`: Grant read/write access to `base.group_system`.
4. **Backend Views**:
   - `custom_addons/ncollection_core/views/error_log_views.xml`: Odoo 19 `<list>` and `<form>` views under Technical / Logging menu.

### Component B: `ncollection_branding` — Uniform Glassmorphic Error Templates
1. **Master Layout `ncollection_error_layout`** (`custom_addons/ncollection_branding/views/http_error_templates.xml`):
   - 100% self-contained inline styling + SVG icons (zero asset bundle dependencies).
   - Dynamic `--nc-primary` brand theme synchronization.
   - 1-click **"Copy Error Code"** button with clipboard feedback.
   - Action buttons: Primary CTA ("Return to Dashboard"), Secondary CTA ("Contact Support").
2. **HTTP Overrides**:
   - Inherits `http_routing.404`, `http_routing.403`, `http_routing.500`, `http_routing.4xx` to render `ncollection_error_layout` with status codes, vector icons, and copyable incident IDs.

---

## 3. Verification & Acceptance Criteria
1. Visiting nonexistent routes (`/nonexistent_page`) renders the NCollection 404 template with no Odoo leak.
2. Visiting unauthorized routes renders the NCollection 403 template with permission explanation.
3. Server error exceptions generate an `ERR-XXXXXX-XXXX` trace ID displayed on the page and recorded in `ncollection.error.log`.
4. Masked tracebacks verify passwords and bearer tokens are never logged in plaintext.
5. All local and full-estate test suites pass 100%.
