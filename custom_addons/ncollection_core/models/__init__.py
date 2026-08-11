# -*- coding: utf-8 -*-
# Models for ncollection_core land here over Phase 1
# (P1-T08 roles, P1-T09 workspace config, P1-T10 license enforcement,
#  P1-T11 apps/settings stripping, P1-T12 owner workspace settings).
from . import workspace_config
from . import ir_ui_menu
from . import license_enforcement
from . import ir_http
from . import subscription_gate
from . import res_users
# #374 the financial-data role gate, shared by ncollection_account_dashboard
# and ncollection_ai so the rule has ONE definition rather than two that drift.
from . import financial_gate
# Tenant-side config-sync credential installer — shared by the provisioning seed
# and the #221 re-key job so the security-critical apikey write has ONE home.
from . import config_sync_key
# P1-T17 customer dashboard — self-contained subtree, see dashboard/__init__.py
from . import dashboard
# P4-T01 aggregation engine — self-contained subtree, see aggregation/__init__.py
from . import aggregation
# P4-T02 operational KPIs — self-contained subtree, see kpi/__init__.py
from . import kpi
# P5-T04 anomaly detection — self-contained subtree, see anomaly/__init__.py
from . import anomaly
