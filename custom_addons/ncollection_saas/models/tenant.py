# -*- coding: utf-8 -*-
"""Tenant SaaS-layer behaviour (P2-T02).

The base ncollection.tenant model (fields, lifecycle) lives in
ncollection_subscription. Here we add the provisioning-pipeline concerns that
belong with the SaaS runner: deriving a routable database name from the company
name, the welcome email on success, and the admin alert on failure. Kept in
ncollection_saas because only this layer depends on queue_job / the engine.
"""

import logging
import re
import unicodedata

from odoo import models

_logger = logging.getLogger(__name__)

# Names a generated tenant DB must never take: reserved words + the fixture
# namespaces owned by the routing/e2e/provisioning verification suites (whose
# *-clean targets could later drop a same-named real tenant) + the demo/CI
# databases. The live platform DB (env.cr.dbname) is added at runtime. The
# generated name is ALSO strictly alphanumeric (no underscore) so it is a valid
# subdomain — tenant key === subdomain === database name.
_GENERATED_NAME_BLOCKLIST = frozenset({
    'admin', 'www', 'staging', 'api', 'postgres', 'template0', 'template1',
    'ncollection', 'ncollectiondemo', 'ncplatform', 'albarari', 'saastest', 'citest',
    'rtclienta', 'rtclientb', 'rtadmin',
    'e2eclienta', 'e2eclientb', 'e2eadmin',
    'provclient', 'provfail',
})
_GENERATED_NAME_RE = re.compile(r'^[a-z][a-z0-9]{2,62}$')


class Tenant(models.Model):
    _inherit = 'ncollection.tenant'

    # ---- database-name generation (P2-T02 point 1) -----------------------

    def _ensure_database_name(self):
        """Give the tenant a valid, routable, collision-free DB name if it has
        none (or an unroutable one). Idempotent: a name that already validates
        is kept."""
        self.ensure_one()
        current = self.database_name
        if current and _GENERATED_NAME_RE.match(current):
            return current
        name = self._generate_database_name(self.company_name)
        self.database_name = name
        return name

    def _generate_database_name(self, company_name):
        """Slugify a company name to ^[a-z][a-z0-9]{2,62}$, then de-collide.

        Collision space = the live cluster (pg_database) + the reserved/fixture
        blocklist + the platform DB — a numeric suffix is appended until free.
        """
        base = self._slugify_db_name(company_name)
        blocked = _GENERATED_NAME_BLOCKLIST | {self.env.cr.dbname}
        Job = self.env['ncollection.provisioning.job']
        candidate = base
        suffix = 1
        while candidate in blocked or Job._database_exists(candidate):
            suffix += 1
            tail = str(suffix)
            candidate = '%s%s' % (base[:63 - len(tail)], tail)
        return candidate

    @staticmethod
    def _slugify_db_name(name):
        """ASCII-fold, strip to alphanumeric, guarantee a letter start and the
        3–63 length window."""
        folded = unicodedata.normalize('NFKD', name or '').encode('ascii', 'ignore').decode()
        slug = re.sub(r'[^a-z0-9]+', '', folded.lower())
        if not slug or not slug[0].isalpha():
            slug = 'nc' + slug
        slug = slug[:63]
        if len(slug) < 3:
            slug = (slug + 'tenant')[:63]
        return slug

    # ---- notifications (P2-T02 points 3 & 4) -----------------------------

    def _send_welcome_email(self, setup_url=None):
        """Queue the branded welcome email (never fail provisioning on mail
        transport — the user_invite.py precedent). Dev has no SMTP, so this
        produces a queued mail.mail row rather than a delivery."""
        self.ensure_one()
        template = self.env.ref(
            'ncollection_saas.mail_template_tenant_welcome', raise_if_not_found=False)
        if not template or not self.email:
            return
        try:
            template.with_context(nc_setup_url=setup_url or '').send_mail(
                self.id,
                email_layout_xmlid='mail.mail_notification_light',
                force_send=False,
            )
        except Exception:  # noqa: BLE001 - transport must never break the flow
            _logger.warning(
                "Welcome email could not be queued for tenant %s (no mail server?)",
                self.id, exc_info=True)

    def _notify_provisioning_failure(self, log):
        """Alert the platform admins that provisioning failed, with the job log.

        Posts to the tenant chatter (durable, visible on the record) and best-
        effort emails the system-admin group. Transport failures are swallowed."""
        self.ensure_one()
        excerpt = (log or '')[-1500:]
        body = self.env._(
            "Provisioning FAILED for tenant %(name)s (database '%(db)s'). "
            "Latest job log:\n%(log)s",
            name=self.company_name, db=self.database_name or '-', log=excerpt)
        try:
            self.message_post(body=body.replace('\n', '<br/>'))
            admins = self.env.ref('base.group_system').all_user_ids
            partners = admins.partner_id
            if partners:
                self.message_notify(
                    partner_ids=partners.ids,
                    subject=self.env._("Tenant provisioning failed: %s", self.company_name),
                    body=body.replace('\n', '<br/>'),
                )
        except Exception:  # noqa: BLE001 - alerting must never mask the failure
            _logger.warning("Could not post provisioning-failure alert for tenant %s",
                            self.id, exc_info=True)
