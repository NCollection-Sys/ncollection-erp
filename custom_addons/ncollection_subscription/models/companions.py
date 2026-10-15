# -*- coding: utf-8 -*-
"""Companion modules: a feature that is entitled BY ITS HOST and never on its own.

THE PROBLEM. Some native modules are not something a customer buys; they are a part of something they already have.
Cheque printing is an action on an Accounting payment plus the layouts it needs, so a tenant that has Accounting
should have it, and a tenant that does not have Accounting must not. Naming it in each plan's module list would copy
the rule into every plan (and every plan an operator makes later), and would let a plan name it WITHOUT Accounting,
which licenses ``account`` through the dependency closure Ring 1 expands at read time: a plan widened by accident.

THE SHAPE. One table, ``COMPANION_MODULES``: ``{companion: host}``. It is applied in exactly one place,
``ncollection.tenant._nc_effective_module_list`` (the single authority provisioning, the module-install job, config
sync and the display field all read), after the plan, the localization package and the per-tenant overrides:

    plan UNION localization MINUS disabled UNION added  ->  UNION every companion whose host is in that list

So a companion is present exactly when its host is, and for every consumer at once. It is the same idea as the country
packages (``localization.py``): entitlement that is DERIVED, not typed, and therefore not offered in the plan picker.

WHAT IT DELIBERATELY IS NOT.
  * Not a second licensing system. Ring 1 / Ring 2 already follow ``account`` (the companion's menus sit under the
    Accounting root and its only business record is ``account.payment``); this only makes sure the module is INSTALLED
    in the tenants that have the host.
  * Not switchable on its own. A plan that names a companion has the name ignored, a per-tenant override naming it is
    ignored, and the picker never offers it: to withhold it, withhold the host.
  * Not an uninstaller. Losing the host hides the companion with it (revocation is not uninstallation, see
    ncollection_saas/models/module_install.py), and nothing here drops a module.

Adding another companion is one entry here (plus its manifest), with no change to provisioning.
"""

# companion module -> the module whose entitlement it follows. Keys are technical module names.
COMPANION_MODULES = {
    'ncollection_cheque_print': 'account',
}


def companions_of(modules):
    """The companions entitled by ``modules`` (an iterable of technical names), in table order."""
    present = set(modules)
    return [companion for companion, host in COMPANION_MODULES.items() if host in present]


def companion_host(module_name):
    """The host a companion follows, or None when ``module_name`` is not a companion."""
    return COMPANION_MODULES.get(module_name)
