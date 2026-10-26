# -*- coding: utf-8 -*-
"""Platform administrator -> tenant entry (tenant side).

A Platform Administrator administers many tenants from ONE platform account. This is a
database-per-tenant system, so "entering" a tenant is not a company switch: the browser is sent to the
tenant's own subdomain (which db_filter routes to the tenant's own database) and signs in there.

NO password is stored or replayed. The flow is:

1. The platform (``ncollection_saas``) checks that the platform user is authorised for that tenant,
   makes a one-time random token and pushes ONLY ITS SHA-256 to this database over the existing
   per-tenant, workspace-scoped service channel (json2 + HMAC-derived bearer): :meth:`grant_from_platform`.
2. The browser opens ``/ncollection/platform/enter#token=...`` on this tenant (a URL fragment, so no server, proxy
   or log ever sees it; a bridge page POSTs it). The token is the credential
   of a dedicated, passwordless *proxy user* (one per platform administrator, ``platform.<ref>``) and is
   redeemed atomically, exactly once, within its short lifetime (:meth:`_redeem`).
3. Everything the administrator then does is written by that proxy user, so create_uid / write_uid /
   chatter / audit rows name "Platform Admin: <person>" and not an anonymous tenant Admin.

The proxy user cannot sign in by password (none is set, and a password credential is refused outright), is
excluded from the plan's seat count, and its session ends ``SESSION_MAX_SECONDS`` after the entry (the session
token stops matching, see ResUsers._compute_session_token).
"""
import hashlib
import re

from odoo import api, fields, models
from odoo.exceptions import AccessDenied, UserError

CREDENTIAL_TYPE = 'ncollection_platform_entry'
PROXY_LOGIN_PREFIX = 'platform.'
SESSION_MAX_SECONDS = 8 * 3600

_REF_RE = re.compile(r'^u[0-9]{1,23}$')                           # what the platform emits: 'u<user id>'
_HASH_RE = re.compile(r'^[0-9a-f]{64}$')
_URL_RE = re.compile(r'^https?://[^\s/?#@]+(?:[/?#]\S*)?$')       # no user-info ("https://trusted@evil")
_MIN_TTL, _MAX_TTL, _DEFAULT_TTL = 10, 120, 60
_GRANT_KEYS = {'token_hash', 'platform_ref', 'platform_name', 'return_url', 'ttl'}


def token_digest(token):
    """The only form of a token this database ever stores."""
    return hashlib.sha256((token or '').encode()).hexdigest()


class PlatformEntry(models.Model):
    _name = 'ncollection.platform.entry'
    _description = 'Platform administrator entry grant'
    _order = 'id desc'

    token_hash = fields.Char(required=True, index=True, readonly=True)
    platform_ref = fields.Char(required=True, readonly=True,
                               help="Stable id of the platform administrator (not a password, not a login).")
    platform_name = fields.Char(required=True, readonly=True)
    return_url = fields.Char(readonly=True, help="Where 'Back to Platform' goes (set by the platform).")
    expires_at = fields.Datetime(required=True, readonly=True)
    used_at = fields.Datetime(readonly=True)
    user_id = fields.Many2one('res.users', string='Proxy user', readonly=True, ondelete='set null', index=True)
    remote_ip = fields.Char(readonly=True)
    state = fields.Selection(
        [('granted', 'Granted'), ('used', 'Used'), ('expired', 'Expired')], compute='_compute_state')

    _token_hash_unique = models.Constraint('unique(token_hash)', 'A grant token can only be registered once.')

    @api.depends('used_at', 'expires_at')
    def _compute_state(self):
        now = fields.Datetime.now()
        for entry in self:
            entry.state = 'used' if entry.used_at else ('expired' if entry.expires_at <= now else 'granted')

    # ------------------------------------------------------------------ platform -> tenant
    @api.model
    def grant_from_platform(self, vals):
        """Register one entry grant. Called over json2/bearer by the config-sync service account ONLY
        (it holds create on this model and nothing else); anyone else is refused by the ACL.

        The platform has already proven that its user may enter this tenant; this method only validates the
        SHAPE of what it was sent, so a compromised caller still cannot smuggle a password, a user id, a group
        or an arbitrary redirect in.
        """
        self.check_access('create')            # first of all: only the service account holds this right
        # Stale, never-used grants are noise: drop them. Used grants stay: they are the audit trail.
        stale = fields.Datetime.subtract(fields.Datetime.now(), days=1)
        self.sudo().search([('used_at', '=', False), ('expires_at', '<', stale)]).unlink()
        entry = self.sudo().create(self._grant_vals(vals))     # create() runs the same check for any other caller
        return {'ok': True, 'id': entry.id}

    @api.model
    def _grant_vals(self, vals):
        """The ONLY values a grant may be created from: validated shape, a clamped lifetime, nothing else.

        Every caller that is not the superuser goes through here (see :meth:`create`), so the service account
        cannot skip these checks by calling the public ``create`` over json2 instead of ``grant_from_platform``:
        it cannot set an expiry, a used flag, a user or an address of its own.
        """
        if not isinstance(vals, dict) or set(vals) - _GRANT_KEYS:
            raise UserError(self.env._("Malformed entry grant."))
        token_hash = str(vals.get('token_hash') or '')
        ref = str(vals.get('platform_ref') or '')
        name = (str(vals.get('platform_name') or '').strip())[:80]
        return_url = str(vals.get('return_url') or '')
        if not _HASH_RE.fullmatch(token_hash) or not _REF_RE.fullmatch(ref) or not name:
            raise UserError(self.env._("Malformed entry grant."))
        if return_url and not _URL_RE.fullmatch(return_url):
            raise UserError(self.env._("Malformed entry grant."))
        try:
            ttl = int(vals.get('ttl') or _DEFAULT_TTL)
        except (TypeError, ValueError):
            ttl = _DEFAULT_TTL
        ttl = max(_MIN_TTL, min(_MAX_TTL, ttl))
        return {
            'token_hash': token_hash, 'platform_ref': ref, 'platform_name': name, 'return_url': return_url,
            'expires_at': fields.Datetime.add(fields.Datetime.now(), seconds=ttl),
        }

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            vals_list = [self._grant_vals(vals) for vals in vals_list]
            self = self.sudo()                 # the right to create was checked by the ACL; the values are now ours
        return super().create(vals_list)

    # ------------------------------------------------------------------ browser -> tenant
    @api.model
    def _lookup_open(self, token):
        """The open (unused, unexpired) grant for ``token``, or an empty recordset. Read-only."""
        if not token or len(token) > 200:
            return self.browse()
        return self.sudo().search([
            ('token_hash', '=', token_digest(token)), ('used_at', '=', False),
            ('expires_at', '>', fields.Datetime.now())], limit=1)

    @api.model
    def _proxy_user(self, entry):
        """The passwordless proxy user of the platform administrator named by ``entry`` (created on first entry)."""
        Users = self.env['res.users'].sudo().with_context(active_test=False)
        login = PROXY_LOGIN_PREFIX + entry.platform_ref
        # Two first entries of the same administrator at once must not both create the proxy: serialise on the login
        # for the rest of this transaction (no savepoint: its exit flush needs a current user, and there is none here).
        self.env.cr.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ['nc_platform_proxy:' + login])
        user = Users.search([('login', '=', login)], limit=1)
        name = self.env._("Platform Admin: %s", entry.platform_name)
        if user:
            if not user.nc_platform_proxy:
                raise AccessDenied()          # a real user squatting the reserved login: never reuse it
            user.write(self._nc_proxy_refresh_vals(user, name))
            return user
        groups = [self.env.ref('base.group_system').id]
        owner = self.env.ref('ncollection_core.group_role_owner', raise_if_not_found=False)
        if owner:
            groups.append(owner.id)
        groups += self._nc_proxy_platform_group_ids()
        company = self._nc_entered_company()
        return Users.create({
            'name': name, 'login': login, 'nc_platform_proxy': True, 'nc_platform_ref': entry.platform_ref,
            'group_ids': [(6, 0, groups)], 'company_id': company.id, 'company_ids': [(6, 0, company.ids)],
        })

    @api.model
    def _nc_entered_company(self):
        """The company an entry is for. The grant names a TENANT (this database), not a company, and the entry endpoint
        runs without a user, so there is no 'current' company: it is the workspace's main ACTIVE company. Least
        privilege: the proxy gets that one company only, never every company of the tenant by default.

        The main company is Odoo's own ``base.main_company``. It is NOT the first by ``sequence``: that column is NULL
        on the company a database is created with, and Postgres sorts NULL last, so any company added later would
        come first and the proxy would land in the wrong company. The sequence order is only the fallback for a
        workspace whose main company is gone or archived."""
        main = self.env.ref('base.main_company', raise_if_not_found=False)
        if main and main.active:
            return main
        return self.env['res.company'].sudo().search([('active', '=', True)], order='sequence, id', limit=1)

    @api.model
    def _nc_proxy_platform_group_ids(self):
        """Rights a Platform Administrator holds INSIDE a tenant that a tenant Owner does not: the identity of the
        system (ncollection_branding) is the platform's to change, so the passwordless proxy, and only it, holds the
        System Branding Manager group. A tenant's own Owner and Tenant Administrator never do."""
        group = self.env.ref('ncollection_branding.group_system_branding_manager', raise_if_not_found=False)
        return group.ids if group else []

    @api.model
    def _nc_proxy_refresh_vals(self, user, name):
        """What a repeat entry refreshes on the proxy: its displayed name, being active, and its company, which is
        RESET to the entered one, so nothing granted before (another company, an archived one) carries over."""
        vals = {'name': name, 'active': True}
        vals['group_ids'] = [(4, group_id) for group_id in self._nc_proxy_platform_group_ids()]
        company = self._nc_entered_company()
        if user.company_id != company or user.company_ids != company:
            vals.update({'company_id': company.id, 'company_ids': [(6, 0, company.ids)]})
        return vals

    @api.model
    def _redeem(self, user, token, remote_ip=''):
        """Consume the grant for ``token`` on behalf of ``user``: atomic, single use, bound to that user."""
        if not (user.nc_platform_proxy and user.nc_platform_ref and token and len(token) <= 200):
            raise AccessDenied()
        self.flush_model()                     # the raw UPDATE below must see any pending ORM write (e.g. an expiry)
        cr = self.env.cr
        cr.execute(
            "UPDATE ncollection_platform_entry SET used_at = (now() AT TIME ZONE 'UTC'), user_id = %s, remote_ip = %s"
            " WHERE token_hash = %s AND platform_ref = %s AND used_at IS NULL"
            " AND expires_at > (now() AT TIME ZONE 'UTC') RETURNING id",
            [user.id, (remote_ip or '')[:64], token_digest(token), user.nc_platform_ref])
        row = cr.fetchone()
        if not row:
            raise AccessDenied()
        self.invalidate_model()
        return self.browse(row[0])

    @api.model
    def _session_admin_name(self, user):
        """The platform administrator's own name for the banner: what the platform sent, not a parse of the proxy
        name (which is translated)."""
        last = self.sudo().search([('user_id', '=', user.id), ('used_at', '!=', False)], order='used_at desc', limit=1)
        return last.platform_name or user.name

    @api.model
    def _session_is_fresh(self, user):
        """True while user's latest entry is younger than SESSION_MAX_SECONDS (see ResUsers._compute_session_token)."""
        last = self.sudo().search([('user_id', '=', user.id), ('used_at', '!=', False)], order='used_at desc', limit=1)
        return bool(last) and fields.Datetime.now() <= fields.Datetime.add(last.used_at, seconds=SESSION_MAX_SECONDS)
