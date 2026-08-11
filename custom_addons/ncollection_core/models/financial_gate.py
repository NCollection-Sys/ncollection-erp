# -*- coding: utf-8 -*-
"""The financial-data role gate, in ONE place (#374).

WHY THIS MODULE EXISTS. The same authorization rule was written out twice, in
two modules that do not depend on each other:

  * ncollection_account_dashboard/models/dashboard_service.py — the helper plus
    the group tuple, at three call sites
  * ncollection_ai/models/ai_question.py — a local `_ALLOWED_GROUPS` tuple and
    an inline `has_group` check

They agreed. The risk was never today's behaviour, it was drift: the next time
the financial-role definition changes, one copy gets updated and the other
silently does not — and the one that does not is an authorization check, so the
failure is a data exposure rather than a broken feature.

#60 could not fix it at the time: ncollection_ai depends only on
ncollection_core, so importing the helper from a DASHBOARDS module would have
pointed the dependency the wrong way. Re-implementing locally was the right call
then. Both modules already depend on ncollection_core, which is the natural
home, so the duplication ends here.

TWO HELPERS, KEPT DISTINCT ON PURPOSE. They differ over whether the Owner gets
the `base.group_system` escape hatch, and collapsing them reintroduces #356.
Read both docstrings before touching either.
"""
from odoo import models
from odoo.exceptions import AccessError

# The financial-data gate: who may see company-wide money.
#
# `base.group_system` is NOT boilerplate. `admin` holds it and holds NO role
# group — the implication runs owner -> system, never the reverse — so omitting
# it locks admin out on every tenant. It also breaks the suites: tests that do
# not use `with_user` run as uid 1 (`__system__`), which holds exactly that
# group. Measured on #333: dropping the clause produced 8 failures, only one of
# which was the test written for it.
FINANCIAL_GATE_GROUPS = (
    'ncollection_core.group_role_accountant',
    'ncollection_core.group_role_ceo',
    'base.group_system',
)


class NcFinancialGateMixin(models.AbstractModel):
    _name = 'ncollection.financial.gate.mixin'
    _description = 'Shared role gate for financial data (#374)'

    def _require_any_group(self, xmlids, message):
        """Raise AccessError unless the caller holds one of `xmlids` (#333).

        Used by all four role-curated dashboards and by the AI assistant. #333
        mirrored the CEO menu; #356 did the same for sales/HR/warehouse, which
        is why this is a helper — four copies of the same five lines is how two
        of them end up subtly different, and #374 is that having happened
        across module boundaries as well.

        EVERY financial caller passes `base.group_system` alongside its role,
        for the reason recorded on FINANCIAL_GATE_GROUPS above.

        `AccessError`, not `UserError`: Odoo documents it as the access-rights
        error and maps it to HTTP 403, which is what an RPC caller should see
        for an authorization refusal.
        """
        if not any(self.env.user.has_group(x) for x in xmlids):
            raise AccessError(message)

    def _require_role_or_technical_admin(self, role_xmlid, message):
        """Allow `role_xmlid`, or a TECHNICAL admin — but not the Owner (#356).

        WHY THIS IS NOT JUST _require_any_group WITH base.group_system.
        `group_role_owner` IMPLIES `base.group_system` directly
        (ncollection_core/security/role_groups.xml), so that clause admits
        every Owner. On the CEO dashboard that is harmless — Owner qualifies
        there anyway, through `group_role_ceo` — and its docstring says so.
        The precondition does not hold for the department dashboards: nothing
        implies group_role_sales/hr/warehouse for an Owner. Reusing the clause
        unmodified therefore let Owner through them while the ruling, the
        comments and the commit message all said Owner was denied. Caught by
        the security review; the tests did not cover Owner at all, so nothing
        else would have.

        THE ROLE IS CHECKED FIRST, AND THAT ORDER MATTERS. A user may hold BOTH
        the Owner role and a department role — that person DOES see the menu,
        because they hold the group it names, so they must pass here too.
        Excluding Owner from the role branch would deny someone the menu shows,
        which is the same Rule-4 mismatch pointing the other way. The exclusion
        applies only to the technical-admin escape hatch.
        """
        user = self.env.user
        if user.has_group(role_xmlid):
            return
        if (user.has_group('base.group_system')
                and not user.has_group('ncollection_core.group_role_owner')):
            return
        raise AccessError(message)
