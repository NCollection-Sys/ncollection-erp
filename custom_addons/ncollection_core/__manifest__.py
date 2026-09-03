{
    'name': 'NCollection Core',
    # 1.10.0 (#221): tenant-side config-sync credential installer
    # (ncollection.config.sync.key) — THE single definition of the apikey write,
    # shared by the provisioning seed and the re-key job.
    # 1.11.0 (#101/P10-T09): sync_from_platform also applies pushed reseller
    # branding onto res.company (override-if-default). Both landed on 1.10.0
    # independently; bumped to 1.11.0 to keep a single monotonic version.
    # 19.0.1.12.0 (#308): sync_from_platform also applies a pushed ECB rate,
    # derived against the tenant's own USD peg row. No schema change.
    # 19.0.1.13.0 (#61/P5-T04): ncollection.alert + four anomaly detectors
    # + two daily crons. New model and new columns, so `-u` is what applies
    # it to an existing tenant database.
    # 19.0.1.14.0 (#346): role-scoped alert visibility — 4 ir.rule records,
    # role ACL rows and a menu. Security policy change, so `-u` is what
    # applies it; a tenant left un-upgraded keeps alerts admin-only.
    # 19.0.1.15.0 (#347): adds the cron service user and binds the anomaly
    # crons to it. A DATA file, so `-u` is what applies it; a tenant left
    # un-upgraded keeps running its crons as superuser with Ring 2 inert.
    # 19.0.1.15.2 (#347): 15.0 gave that user a REAL password — the XML wrote
    # `<field name="password">False</field>` with no eval=, so the loader stored
    # the literal string "False" and hashed it, shipping a working credential to
    # every tenant. The field is gone and a post-migrate nulls the column for
    # tenants that already installed 15.0 (the record is noupdate, so an upgrade
    # will not rewrite it on its own). The same migration also adds the
    # account to the new group_cron_service, without which it cannot
    # create ncollection.alert and every detected anomaly is silently
    # discarded.
    # 19.0.1.16.0 (#374): ncollection.financial.gate.mixin — the financial-data
    # role gate, previously written out twice (dashboard_service.py and
    # ncollection_ai/ai_question.py). An AbstractModel with no table and no
    # data/view/security records, so unlike every entry above it needs NO `-u`:
    # a restart picks it up, and a tenant left un-upgraded behaves identically
    # because the admitted group set is unchanged.
    'version': '19.0.1.16.0',
    'category': 'Hidden',
    'summary': 'Core access rights and security for NCollection ERP',
    'author': 'NCollection',
    'website': 'https://ncollection.com',
    'license': 'LGPL-3',
    # ncollection_branding (P1-T16): the Workspace Appearance page edits the
    # res.company nc_* branding fields defined by ncollection_branding. Per the
    # DELIVERABLE_1 §2.5 matrix branding ships in every tenant DB where core
    # lives, so the edge is core -> branding (branding stays installable
    # standalone in the admin DB, so the reverse would break its admin install).
    'depends': ['base', 'web', 'ncollection_branding'],
    'data': [
        'security/role_groups.xml',
        'security/config_sync_security.xml',
        # Before ir.model.access.csv: that file has a row keyed on
        # group_cron_service, and a CSV cannot forward-reference a
        # group the loader has not created yet (#347).
        'security/cron_service_security.xml',
        'security/ir.model.access.csv',
        # P5-T04 follow-up (#346): record rules scoping alerts by role.
        # After ir.model.access.csv, because a rule is meaningless
        # without the ACL that lets the group reach the model at all.
        'security/alert_security.xml',
        'views/workspace_settings_views.xml',
        'views/workspace_appearance_views.xml',
        'views/subscription_blocked_templates.xml',
        'views/dashboard_action.xml',
        # #455: loaded AFTER the dashboard so the launcher's sequence="0" menu
        # is created against an existing sibling set; order here is not
        # load-bearing for the sort itself (sequence decides that), only for
        # readability of the data files.
        'views/home_action.xml',
        'data/kpi_data.xml',
        # P5-T04 anomaly detection. (Order is cosmetic here: init_models()
        # reflects every Python model into ir.model before ANY of a module's
        # data files load, so model_id ref= would resolve either way.)
        'views/alert_views.xml',
        # Must load BEFORE any cron that binds to it.
        'data/cron_user.xml',
        'data/anomaly_cron.xml',
        # P8-T10 error telemetry & structured incident logging (Issue #444)
        'views/error_log_views.xml',
    ],
    # P1-T17 customer dashboard. NOTE the deliberate absence of new entries in
    # 'depends': the dashboard reads sale/account/crm through SOFT checks
    # (see models/dashboard/dashboard_data.py), so it never forces those apps
    # into a tenant's module set and leaves P1-T09/T10 licensing untouched.
    'assets': {
        'web.assets_backend': [
            # Chart.js ships with Odoo (web/static/lib/Chart/Chart.js) but is
            # not in the backend bundle by default. Listing it is idempotent —
            # Odoo de-duplicates asset paths — and avoids a runtime dependency
            # on some other module happening to pull it in first.
            'web/static/lib/Chart/Chart.js',
            'ncollection_core/static/src/dashboard/dashboard.scss',
            'ncollection_core/static/src/dashboard/dashboard.js',
            'ncollection_core/static/src/dashboard/dashboard.xml',
            # #455 tenant application launcher.
            'ncollection_core/static/src/home/tenant_home.scss',
            'ncollection_core/static/src/home/tenant_home.js',
            'ncollection_core/static/src/home/tenant_home.xml',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'auto_install': False,
}
