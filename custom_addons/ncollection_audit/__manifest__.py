# pylint: disable=manifest-required-author
# (C8101 wants the OCA as author; this is a proprietary NCollection module.)
{
    'name': 'NCollection Audit Trail',
    'version': '19.0.1.0.0',
    'category': 'Tools',
    'summary': 'Audit-trail infrastructure on OCA auditlog: client IP, seeded '
               'rules, retention and tamper evidence (P8-T05)',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # NO repos.yml CHANGE. `auditlog` was pinned FOR THIS TICKET at P1-T04 —
    # repos.yml says "Consumed: auditlog — audit trail, wired at P8-T05" — and
    # DELIVERABLE_1_SYSTEM_DESIGN.md:373 names OCA/server-tools -> auditlog for
    # P8-T05. The build-vs-buy question was answered before this branch existed;
    # `oca-scout` re-verified the pin resolves, that the module is on the 19.0
    # branch at our exact commit, and that it declares no external_dependencies
    # (so the queue_job/openupgradelib trap does not repeat here).
    #
    # THIS MODULE IS THE GAP LIST. auditlog gives us old/new values, the acting
    # user, a timestamp and registry-level ORM hooking. It does NOT give us:
    #
    #   1. the client IP     — the acceptance criterion names it; there is no
    #                          IP field anywhere in auditlog (grepped: zero
    #                          hits for remote_addr/ip_address).
    #   2. rules as DATA     — auditlog.rule is UI-only; nothing seeds it, and
    #                          a fleet cannot be configured by clicking.
    #   3. retention         — its autovacuum cron ships `active=False` with a
    #                          hardcoded 180 days.
    #   4. tamper evidence   — none at all, and its own manager group holds
    #                          perm_unlink=1 on auditlog.log.
    #
    # DEPENDS ON `base` ONLY BEYOND auditlog. Deliberately NOT on `account` or
    # `sale`: this is platform infrastructure that must install on a tenant DB
    # and on the admin DB alike, and those two have different models. Which
    # rules get seeded is decided at runtime from what actually exists (see
    # models/audit_rule.py) — declaring `account` here would drag the whole
    # accounting stack onto the platform database to audit four tenant models.
    'depends': ['auditlog'],
    'data': [
        'security/ir.model.access.csv',
        'security/audit_security.xml',
        'data/ir_cron.xml',
        'views/audit_seal_views.xml',
    ],
    'post_init_hook': 'post_init_hook',
}
