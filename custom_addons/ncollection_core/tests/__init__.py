# -*- coding: utf-8 -*-
from . import test_smoke
from . import test_roles
from . import test_workspace_config
from . import test_menu_visibility
from . import test_license_enforcement
from . import test_menu_stripping
from . import test_workspace_settings
from . import test_dashboard
from . import test_workspace_appearance
from . import test_subscription_gate
from . import test_auth_signup
from . import test_aggregation_engine
from . import test_aggregation_cache
from . import test_kpi
from . import test_config_sync_key
from . import test_reseller_branding_apply
from . import test_pushed_rate
from . import test_anomaly_statistics
from . import test_anomaly_acceptance
from . import test_anomaly_visibility
# #374 — the shared financial gate is wired to its consumers, not re-copied
from . import test_financial_gate
# P6-T02 (#66) — portal users see only their own records
from . import test_portal_isolation
# P8-T10 (#444) — error log & incident correlation tests
from . import test_error_log
from . import test_tenant_home
from . import test_module_management_lockout
from . import test_platform_entry
from . import test_assets_compile
from . import test_platform_proxy_credentials
from . import test_launcher_apps
from . import test_legacy_base_url
from . import test_home_ui
from . import test_platform_audit
from . import test_workspace_admin
from . import test_admin_service
from . import test_forced_password_change
from . import test_schema_drift
from . import test_system_branding_boundary
from . import test_release_gate
from . import test_release_gate_cron
from . import test_app_url
