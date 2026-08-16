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
