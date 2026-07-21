# -*- coding: utf-8 -*-
"""Customer Workspace Dashboard — data service (P1-T17).

One RPC (:meth:`get_dashboard_payload`) returns everything the OWL client needs,
so the landing page costs a single round-trip (acceptance: loads under 2s).

Two rules shape this module:

1. **Role gating is server-side.** The payload contains ONLY the widgets the
   caller's role permits. The client renders what it is given and filters
   nothing — UI hiding alone is not security (Standing Rule 4). A widget denied
   by role is absent from the response, not merely hidden.

2. **Dependencies are soft.** ``ncollection_core`` depends on ``base`` and
   ``web`` only. Providers that read another app's models check availability
   first and are simply omitted when that app is not installed. This keeps the
   dashboard out of the tenant module set entirely, so provisioning, P1-T09
   menu visibility and P1-T10 license enforcement are untouched: a CRM-only
   tenant lands on a dashboard without finance widgets, which is the intended
   product behaviour anyway.

Financial providers are interim. FPA §7 assigns them permanently to
``ncollection_account_dashboard``; each carries a HANDOFF marker for #119
(F3-T01) and stays thin — aggregation only, never financial business logic.
"""

from odoo import _, api, models

# --- widget groups ---------------------------------------------------------
# Mirrors demo/src/lib/roles.ts, which is the design reference for this screen
# (demo/README.md maps "Customer Dashboard" to P1-T17).
GROUP_FINANCIAL = 'financial'
GROUP_PIPELINE = 'pipeline'
GROUP_OPERATIONS = 'operations'
GROUP_PERSONAL = 'personal'

ALL_WIDGET_GROUPS = (GROUP_FINANCIAL, GROUP_PIPELINE, GROUP_OPERATIONS, GROUP_PERSONAL)

# Which widget groups each of the 8 P1-T08 roles grants (security/role_groups.xml).
#
# Resolution is a UNION over every role the user holds, which is correct for two
# independent reasons:
#   - the groups declare implied_ids (owner -> ceo -> manager -> employee), so
#     has_group() is true transitively, and
#   - roles are additive by design: one user may hold e.g. Manager + Accountant.
# The union reproduces the demo's per-role sets exactly; verified in tests.
ROLE_WIDGET_GROUPS = {
    'ncollection_core.group_role_employee': (GROUP_PERSONAL,),
    'ncollection_core.group_role_hr': (GROUP_PERSONAL,),
    'ncollection_core.group_role_sales': (GROUP_PIPELINE, GROUP_PERSONAL),
    'ncollection_core.group_role_warehouse': (GROUP_OPERATIONS, GROUP_PERSONAL),
    'ncollection_core.group_role_accountant': (GROUP_FINANCIAL, GROUP_PERSONAL),
    'ncollection_core.group_role_manager': (GROUP_PIPELINE, GROUP_OPERATIONS, GROUP_PERSONAL),
    'ncollection_core.group_role_ceo': ALL_WIDGET_GROUPS,
    'ncollection_core.group_role_owner': ALL_WIDGET_GROUPS,
}


class NCollectionDashboardData(models.AbstractModel):
    """Read-only aggregation service for the customer landing dashboard."""

    _name = 'ncollection.dashboard.data'
    _description = 'NCollection Customer Dashboard Data'

    # -- role resolution ----------------------------------------------------

    @api.model
    def _widget_groups_for_user(self):
        """Return the set of widget groups the current user may see."""
        user = self.env.user
        groups = set()
        for role_xmlid, widget_groups in ROLE_WIDGET_GROUPS.items():
            if user.has_group(role_xmlid):
                groups.update(widget_groups)

        # A system administrator holding no NCollection role still administers
        # the workspace; mirror Owner rather than serving an empty dashboard.
        if not groups and user.has_group('base.group_system'):
            groups.update(ALL_WIDGET_GROUPS)

        # Employee is "the floor every other role stands on" (role_groups.xml),
        # so any internal user sees at least their personal widgets.
        if user.has_group('base.group_user'):
            groups.add(GROUP_PERSONAL)
        return groups

    # -- soft dependency ----------------------------------------------------

    @api.model
    def _model_available(self, model_name):
        """True when ``model_name`` exists in this database's registry.

        The mechanism behind soft dependencies: an app the tenant's plan does
        not include is simply not installed, and its widgets drop out.
        """
        return model_name in self.env

    # -- providers ----------------------------------------------------------

    @api.model
    def _provider_specs(self):
        """Declare the widgets. One dict per widget; order is display order.

        ``model`` is the optional soft dependency; ``compute`` names a method
        returning the widget's value payload.
        """
        return [
            {
                'key': 'open_activities',
                'group': GROUP_PERSONAL,
                'label': _('Open Activities'),
                'icon': 'activity',
                'model': 'mail.activity',
                'compute': '_compute_open_activities',
            },
        ]

    @api.model
    def _compute_open_activities(self):
        """Activities still open for the current user."""
        count = self.env['mail.activity'].search_count([('user_id', '=', self.env.uid)])
        return {'value': count, 'format': 'integer', 'sub': _('Assigned to you')}

    # -- payload ------------------------------------------------------------

    @api.model
    def get_dashboard_payload(self):
        """Return every widget this user is allowed to see, in display order.

        Gating happens HERE, not in the client: a widget the role does not
        permit never reaches the browser.
        """
        allowed_groups = self._widget_groups_for_user()
        widgets = []

        for spec in self._provider_specs():
            if spec['group'] not in allowed_groups:
                continue  # role gating (Standing Rule 4)
            model_name = spec.get('model')
            if model_name and not self._model_available(model_name):
                continue  # soft dependency: app not installed on this tenant
            widget = {
                'key': spec['key'],
                'group': spec['group'],
                'label': spec['label'],
                'icon': spec.get('icon', 'activity'),
            }
            widget.update(getattr(self, spec['compute'])())
            widgets.append(widget)

        return {
            'widgets': widgets,
            'meta': {
                'user_name': self.env.user.name,
                'company_name': self.env.company.name,
                'widget_groups': sorted(allowed_groups),
            },
        }
