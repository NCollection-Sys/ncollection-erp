# -*- coding: utf-8 -*-
"""Company administration for a Workspace Administrator (Option A).

The tenant Owner holds ``group_tenant_admin`` but not ``base.group_erp_manager``, and Odoo lets only the latter write
``res.company``. The Workspace Administration Center's Company, Appearance and Accounting Defaults screens are plain
``res.company`` forms, so without this the Owner could no longer save their own company.

This is the one narrow delegation. A write by a Workspace Administrator runs with elevated rights ONLY when
  * every field written is in the allowlist (``_nc_tenant_admin_company_fields``, extended by the modules that own
    company-level business settings) - never currency, parent company, System Branding or a technical field,
  * every company written is one the user belongs to, and
  * every many2one value written belongs to that company (or a parent of it), so an account, tax or journal of
    another company cannot be attached.
Anything else is not delegated and falls through to Odoo's own access check, which refuses it. A user who already
holds base.group_erp_manager (the platform administrator) takes the native path unchanged. Each delegated write is
audited.
"""
from lxml import etree

from odoo import api, models
from odoo.exceptions import AccessError, ValidationError

TENANT_ADMIN = 'ncollection_core.group_tenant_admin'


class ResCompany(models.Model):
    _inherit = 'res.company'

    @api.model
    def _nc_tenant_admin_company_fields(self):
        """Company fields a Workspace Administrator may change. Modules add their company-level settings."""
        return {
            # identity (Workspace Administration > Company)
            'name', 'street', 'street2', 'city', 'state_id', 'zip', 'country_id', 'phone', 'email', 'website',
            'vat', 'company_registry', 'logo',
            # the tenant's own look (Workspace Administration > Appearance); System Branding is never here
            'nc_primary_color', 'nc_secondary_color', 'nc_sidebar_color', 'nc_login_background', 'nc_stamp',
            # Odoo's own Configure Document Layout (web's base.document.layout writes these through related
            # fields): the layout CHOSEN among the installed report.layout records, never a view of one's own;
            # header / footer / details are sanitized HTML (the Owner holds no sanitize-override group)
            'external_report_layout_id', 'paperformat_id', 'font', 'primary_color', 'secondary_color',
            'report_header', 'report_footer', 'company_details', 'layout_background', 'layout_background_image',
            'nc_footer_left', 'nc_footer_center', 'nc_footer_right',
        }

    def _nc_delegated_company_write(self, vals):
        """True when this write is a Workspace Administrator's change to their own company (see module docstring)."""
        if self.env.su or not vals:
            return False
        user = self.env.user
        if user.has_group('base.group_erp_manager'):
            return False
        if set(vals) - (self._nc_tenant_admin_company_fields() & set(self._fields)):
            return False
        # Unified Settings: each field belongs to ONE section; the Workspace Administrator, or a user that section
        # was delegated to, may change it, and only as far as the platform's level for that section allows.
        from .settings_policy import SECTION_GROUP, company_field_section  # noqa: PLC0415 (same package)
        sections = {company_field_section(name) for name in vals}
        if not user.has_group(TENANT_ADMIN) and not all(
                section in SECTION_GROUP and user.has_group(SECTION_GROUP[section]) for section in sections):
            return False
        if self - user.company_ids:
            raise AccessError(self.env._("You can only change the companies you belong to."))
        policy = self.env['ncollection.settings.policy']
        for section in sections:
            policy._nc_require(section, 'edit')
        return True

    def _nc_check_delegated_values(self, vals):
        """Many2one values must belong to the company being changed (or a parent company)."""
        layout_view = vals.get('external_report_layout_id')
        if layout_view and not self.env['report.layout'].sudo().search_count([('view_id', '=', layout_view)]):
            # the document layout is chosen among the installed layouts; any other QWeb view is refused
            raise ValidationError(self.env._("Choose one of the available document layouts."))
        for name, value in vals.items():
            field = self._fields[name]
            if field.type != 'many2one' or not value:
                continue
            target = self.env[field.comodel_name].sudo().browse(value).exists()
            if not target:
                raise ValidationError(self.env._("The selected %(field)s does not exist.", field=field.string))
            # a record of one company (taxes, journals) or of several (accounts are shared through company_ids)
            owners = self.env['res.company']
            for owner_field in ('company_id', 'company_ids'):
                if owner_field in target._fields and target._fields[owner_field].comodel_name == 'res.company':
                    owners |= target[owner_field]
            for company in self:
                if owners and not (owners & company.parent_ids):
                    raise ValidationError(self.env._(
                        "%(field)s must belong to %(company)s.", field=field.string, company=company.name))

    # ------------------------------------------------------------------ the delegated forms in the browser
    # Odoo's ir.ui.view._postprocess_access_rights (run per user in get_view, AFTER the shared view cache) sets
    # edit="False" on every res.company form for a user without the ACL's write right - everyone but
    # base.group_erp_manager. So the delegation above worked over RPC while its own forms opened read-only. Only for
    # the forms listed here, and only for a user the delegation would accept, the form is made editable again and every
    # field the user may NOT write is forced read-only. The server is unchanged: the ACL, the record rules and the
    # write() checks above decide every save exactly as before.

    @api.model
    def _nc_delegated_form_views(self):
        """{form view xml id: the Settings section it belongs to}. Modules add their own delegated forms."""
        return {
            'ncollection_core.view_workspace_company_form': 'company',
            'ncollection_core.view_workspace_appearance_form': 'appearance',
        }

    @api.model
    def _nc_delegated_writable_fields(self):
        """The company fields THIS user may change through the delegation: the same allowlist, section groups and
        platform level that _nc_delegated_company_write applies to a save."""
        from .settings_policy import RANK, SECTION_GROUP, company_field_section  # noqa: PLC0415 (same package)
        user = self.env.user
        admin = user.has_group(TENANT_ADMIN)
        policy = self.env['ncollection.settings.policy']
        editable = {}
        allowed = set()
        for name in self._nc_tenant_admin_company_fields() & set(self._fields):
            section = company_field_section(name)
            if not admin and not (section in SECTION_GROUP and user.has_group(SECTION_GROUP[section])):
                continue
            if section not in editable:
                editable[section] = RANK[policy._nc_level(section)] >= RANK['edit']
            if editable[section]:
                allowed.add(name)
        return allowed

    @api.model
    def get_view(self, view_id=None, view_type='form', **options):
        result = super().get_view(view_id, view_type, **options)
        if view_type != 'form' or self.env.su or self.env.user.has_group('base.group_erp_manager'):
            return result   # the native path: Odoo's own access rights already apply
        forms = {self.env.ref(xmlid, raise_if_not_found=False) for xmlid in self._nc_delegated_form_views()}
        if result.get('id') not in {view.id for view in forms if view}:
            return result
        writable = self._nc_delegated_writable_fields()
        if not writable:
            return result   # nothing this user may change: the form stays read-only, as Odoo made it
        node = etree.fromstring(result['arch'])
        fields_ = [field for field in node.iter('field')
                   if not any(parent.tag == 'field' for parent in field.iterancestors())]  # not a sub-view's field
        if not any(field.get('name') in writable for field in fields_):
            return result   # nothing on THIS form is the user's to change (another section, or its level is 'view')
        node.set('edit', 'True')
        for field in fields_:
            if field.get('name') not in writable:
                field.set('readonly', 'True')
        result['arch'] = etree.tostring(node, encoding='unicode')
        return result

    def _nc_audit_delegated_write(self, vals):
        self.env.user._nc_audit('admin_settings_change', 'company %s: %s' % (
            ', '.join(str(i) for i in self.ids), ', '.join(sorted(vals))))

    def write(self, vals):
        # A module whose own res.company.write must run elevated too (it sits ABOVE this one in the MRO, e.g. Odoo's
        # account, which sets ir.default as the caller) elevates first - see ncollection_account_core - and this one
        # then sees env.su and passes through: one check, one audit.
        if self._nc_delegated_company_write(vals):
            self._nc_check_delegated_values(vals)
            result = super(ResCompany, self.sudo()).write(vals)
            self._nc_audit_delegated_write(vals)
            return result
        return super().write(vals)
