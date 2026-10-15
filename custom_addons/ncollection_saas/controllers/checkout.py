# -*- coding: utf-8 -*-
"""Public self-service checkout endpoints (P2-T16).

Unauthenticated (auth='public') routes for the onboarding funnel:
pricing -> register -> email verify -> "being prepared" progress -> login URL.

Security posture (ARCHITECTURE_SECURITY §11): every write path validates input,
verifies reCAPTCHA (when configured), enforces trial quotas per IP/email, and
NEVER provisions before the email is verified. JSON routes are CSRF-exempt by
design; the verify link is an idempotent GET. Rate limiting is the Nginx edge's
job (P1-T03) — the independent second layer.
"""

import re

from odoo import fields, http
from odoo.http import request

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
# Defensive length cap on the free-text checkout fields before Tenant.create()
# (#226 M-3) — they are unbounded Char otherwise.
_MAX_FIELD_LEN = 200
# The login is a USERNAME, never an email: the same rule the tenant model
# enforces (ncollection_subscription USERNAME_RE).
USERNAME_RE = re.compile(r'^[a-z][a-z0-9._-]{2,31}$')
_MIN_PASSWORD_LEN = 8
_MAX_PASSWORD_LEN = 128
TRIAL_DAYS = 14
# The base domain and every tenant address come from ONE place: ncollection.tenant._nc_base_domain() /
# _nc_public_origin() (no copy of the parameter key, no built-in default). This used to read an orphan
# 'tenant_base_domain' key (#210) and then a second, hardcoded production default.


class CheckoutController(http.Controller):

    # ---- pages (public HTML) ---------------------------------------------

    @http.route('/pricing', type='http', auth='public', website=False, sitemap=True)
    def pricing(self, **kw):
        plans = request.env['ncollection.subscription.plan'].sudo().search(
            [('active', '=', True)], order='monthly_price asc')
        return request.render('ncollection_saas.checkout_pricing', {'plans': plans})

    @http.route('/nc/checkout', type='http', auth='public', website=False)
    def checkout_form(self, plan=None, cycle='monthly', **kw):
        plans = request.env['ncollection.subscription.plan'].sudo().search(
            [('active', '=', True)], order='monthly_price asc')
        selected = plans.filtered(lambda p: p.code == plan)[:1] or plans[:1]
        site_key = request.env['ir.config_parameter'].sudo().get_param(
            'ncollection_saas.recaptcha_site_key', '')
        return request.render('ncollection_saas.checkout_form', {
            'plans': plans,
            'selected_plan': selected,
            'billing_cycle': 'yearly' if cycle == 'yearly' else 'monthly',
            'recaptcha_site_key': site_key,
            # The ONE base-domain definition (never a literal in the template).
            'base_domain': request.env['ncollection.tenant'].sudo()._nc_base_domain(),
        })

    @http.route('/nc/checkout/pending/<string:tenant_uuid>', type='http',
                auth='public', website=False)
    def checkout_pending(self, tenant_uuid, **kw):
        tenant = request.env['ncollection.tenant'].sudo().search(
            [('tenant_uuid', '=', tenant_uuid)], limit=1)
        if not tenant:
            return request.not_found()
        return request.render('ncollection_saas.checkout_pending', {
            'tenant_uuid': tenant.tenant_uuid,
            'company_name': tenant.company_name,
        })

    @http.route('/nc/checkout/verify/<string:token>', type='http',
                auth='public', website=False)
    def checkout_verify(self, token, **kw):
        tenant = request.env['ncollection.tenant'].sudo()._nc_confirm_checkout(token)
        if not tenant:
            return request.render('ncollection_saas.checkout_verify_invalid', {})
        return request.redirect('/nc/checkout/pending/%s' % tenant.tenant_uuid)

    # ---- JSON APIs -------------------------------------------------------

    @http.route('/nc/checkout/availability', type='jsonrpc', auth='public', methods=['POST'])
    def availability(self, subdomain=None, **kw):
        # deep=False: this public endpoint is hammered, so it stops at the tenant
        # registry (cheap ORM) and skips the per-call psycopg2 probe — the
        # connection-churn half of #226 M-1. Per-request RATE capping stays the
        # nginx edge's job (the 'onboard' zone, from P3-T12 H-1); we don't add a
        # duplicate app-level counter here. register() below runs the
        # authoritative deep check before provisioning.
        available, reason = request.env['ncollection.tenant'].sudo()._nc_subdomain_availability(
            subdomain, deep=False)
        return {'available': available, 'reason': reason}

    @http.route('/nc/checkout/register', type='jsonrpc', auth='public', methods=['POST'])
    def register(self, company=None, contact=None, email=None, subdomain=None,
                 plan=None, cycle='monthly', recaptcha_token=None,
                 username=None, password=None, password_confirm=None, **kw):
        Tenant = request.env['ncollection.tenant'].sudo()
        company = (company or '').strip()
        contact = (contact or '').strip()
        email = (email or '').strip().lower()
        username = (username or '').strip().lower()
        password = password or ''
        password_confirm = password_confirm or ''
        subdomain = Tenant._nc_normalize_subdomain(subdomain)
        source_ip = request.httprequest.remote_addr

        # --- validation (stable error keys the UI translates) ---
        if not company or not email or not username or not password:
            return {'success': False, 'error': 'missing_fields'}
        if (len(company) > _MAX_FIELD_LEN or len(contact) > _MAX_FIELD_LEN
                or len(email) > _MAX_FIELD_LEN):
            return {'success': False, 'error': 'field_too_long'}
        if not EMAIL_RE.fullmatch(email):
            return {'success': False, 'error': 'invalid_email'}
        # --- credentials: a real username, and a password typed twice ---
        # The username is validated, never derived: an address is refused
        # outright rather than quietly turned into a login.
        if '@' in username:
            return {'success': False, 'error': 'username_is_email'}
        if not USERNAME_RE.fullmatch(username):
            return {'success': False, 'error': 'invalid_username'}
        if password != password_confirm:
            return {'success': False, 'error': 'password_mismatch'}
        if len(password) < _MIN_PASSWORD_LEN:
            return {'success': False, 'error': 'password_too_short'}
        if len(password) > _MAX_PASSWORD_LEN:
            return {'success': False, 'error': 'field_too_long'}
        if password.lower() == username:
            return {'success': False, 'error': 'password_same_as_username'}
        if not Tenant._nc_verify_recaptcha(recaptcha_token, source_ip):
            return {'success': False, 'error': 'captcha_failed'}
        available, reason = Tenant._nc_subdomain_availability(subdomain)
        if not available:
            return {'success': False, 'error': 'subdomain_%s' % reason}
        plan_rec = request.env['ncollection.subscription.plan'].sudo().search(
            [('code', '=', plan), ('active', '=', True)], limit=1)
        if not plan_rec:
            return {'success': False, 'error': 'invalid_plan'}
        if Tenant._nc_trial_quota_exceeded(source_ip, email):
            return {'success': False, 'error': 'quota_exceeded'}

        # --- create tenant + DRAFT subscription (no provisioning yet) ---
        today = fields.Date.context_today(request.env.user)
        # Trial length is plan-driven (P2-T12); fall back to the module default
        # when a plan sets no trial_days, preserving prior checkout behaviour.
        trial_days = plan_rec.trial_days or TRIAL_DAYS
        trial_end = fields.Date.add(today, days=trial_days)
        # Hash on arrival: the request waits for approval, possibly for days,
        # and the plaintext must never be stored. Odoo's own password context,
        # so the tenant's res.users verifies it natively once it is installed.
        password_hash = request.env['res.users'].sudo()._crypt_context().hash(password)
        tenant = Tenant.create({
            # PENDING APPROVAL: nothing is provisioned until an administrator
            # approves this request (action_provision). The database does not
            # exist yet and cannot be created by anyone else.
            'registration_state': 'pending',
            'registration_date': fields.Datetime.now(),
            'admin_login': username,
            'admin_password_hash': password_hash,
            'company_name': company,
            'contact_name': contact,
            'email': email,
            # #476: this was writing to a dead free-text Char that nothing
            # read. The signup form always collected a real subdomain; it now
            # lands in the field that actually builds the tenant's URL.
            'subdomain': subdomain,
            'database_name': subdomain,
            'plan_id': plan_rec.id,
            'status': 'trial',
            'database_status': 'not_provisioned',
            'onboarding_stage': 'signup',
            'trial_end_date': trial_end,
            'checkout_source_ip': source_ip,
            'checkout_token': Tenant._nc_new_checkout_token(),
        })
        sub = request.env['ncollection.subscription'].sudo().create({
            'tenant_id': tenant.id,
            'plan_id': plan_rec.id,
            'billing_cycle': 'yearly' if cycle == 'yearly' else 'monthly',
            'status': 'draft',
            'start_date': today,
            'end_date': trial_end,
        })
        tenant.subscription_id = sub.id
        tenant._nc_send_verification_email()
        return {'success': True, 'tenant_uuid': tenant.tenant_uuid,
                'state': 'pending'}

    @http.route('/nc/checkout/status', type='jsonrpc', auth='public', methods=['POST'])
    def status(self, tenant_uuid=None, **kw):
        tenant = request.env['ncollection.tenant'].sudo().search(
            [('tenant_uuid', '=', tenant_uuid)], limit=1)
        if not tenant:
            return {'status': 'unknown'}
        # `stage` is the one answer the page needs: pending / rejected /
        # approved / provisioning / ready / failed. `status` is kept for older
        # callers.
        result = {'status': tenant.database_status,
                  'stage': tenant.provision_stage,
                  'verified': tenant.checkout_email_verified}
        if tenant.database_status == 'ready':
            origin = tenant._nc_public_origin()
            if origin:
                result['login_url'] = origin + '/odoo'
        return result
