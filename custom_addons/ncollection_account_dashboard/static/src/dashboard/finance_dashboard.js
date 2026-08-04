/** @odoo-module **/

import { registry } from "@web/core/registry";
import { NcFinancialDashboard } from "./dashboard_base";
import { _t } from "@web/core/l10n/translation";

/** Finance dashboard — the eight Financial Summary KPIs + revenue/expense trend. */
export class FinanceDashboard extends NcFinancialDashboard {
    get serviceMethod() {
        return "get_finance_dashboard";
    }
    get title() {
        return _t("Finance Dashboard");
    }
}

registry.category("actions").add("ncollection_account_dashboard.finance", FinanceDashboard);
