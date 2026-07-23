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
TRIAL_DAYS = 14
_BASE_DOMAIN_PARAM = 'ncollection_saas.tenant_base_domain'


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
        available, reason = request.env['ncollection.tenant'].sudo()._nc_subdomain_availability(subdomain)
        return {'available': available, 'reason': reason}

    @http.route('/nc/checkout/register', type='jsonrpc', auth='public', methods=['POST'])
    def register(self, company=None, contact=None, email=None, subdomain=None,
                 plan=None, cycle='monthly', recaptcha_token=None, **kw):
        Tenant = request.env['ncollection.tenant'].sudo()
        company = (company or '').strip()
        contact = (contact or '').strip()
        email = (email or '').strip().lower()
        subdomain = Tenant._nc_normalize_subdomain(subdomain)
        source_ip = request.httprequest.remote_addr

        # --- validation (stable error keys the UI translates) ---
        if not company or not email:
            return {'success': False, 'error': 'missing_fields'}
        if not EMAIL_RE.match(email):
            return {'success': False, 'error': 'invalid_email'}
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
        tenant = Tenant.create({
            'company_name': company,
            'contact_name': contact,
            'email': email,
            'domain': subdomain,
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
        return {'success': True, 'tenant_uuid': tenant.tenant_uuid}

    @http.route('/nc/checkout/status', type='jsonrpc', auth='public', methods=['POST'])
    def status(self, tenant_uuid=None, **kw):
        tenant = request.env['ncollection.tenant'].sudo().search(
            [('tenant_uuid', '=', tenant_uuid)], limit=1)
        if not tenant:
            return {'status': 'unknown'}
        result = {'status': tenant.database_status, 'verified': tenant.checkout_email_verified}
        if tenant.database_status == 'ready':
            base_domain = request.env['ir.config_parameter'].sudo().get_param(
                _BASE_DOMAIN_PARAM, 'localhost')
            result['login_url'] = 'https://%s.%s/odoo' % (tenant.database_name, base_domain)
        return result
