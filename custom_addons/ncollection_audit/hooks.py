# -*- coding: utf-8 -*-
"""P8-T05 install hook.

Seeds the audit rules once at install. `models/audit_rule.py`'s
`_register_hook` is what keeps them seeded afterwards — R-014 is the record of
what happens when a post-init hook is the ONLY seeding path and the models it
cares about arrive later.

SIGNATURE: Odoo 19 calls `post_init_hook(env)`, not the `(cr, registry)` pair
older versions used — `odoo/modules/loading.py` does
`getattr(py_module, post_init)(env)`. The old form fails at install with
`TypeError: post_init_hook() missing 1 required positional argument`, which is
loud rather than silent, but only if somebody installs the module.
"""


def post_init_hook(env):
    env['auditlog.rule']._nc_seed_rules()
