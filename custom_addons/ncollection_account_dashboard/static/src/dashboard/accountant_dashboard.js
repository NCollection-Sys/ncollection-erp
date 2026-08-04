/** @odoo-module **/

import { registry } from "@web/core/registry";
import { NcFinancialDashboard } from "./dashboard_base";
import { _t } from "@web/core/l10n/translation";

/** Accountant dashboard — profitability KPIs (incl. service-computed margins). */
export class AccountantDashboard extends NcFinancialDashboard {
    get serviceMethod() {
        return "get_accountant_dashboard";
    }
    get title() {
        return _t("Accountant Dashboard");
    }
}

registry.category("actions").add("ncollection_account_dashboard.accountant", AccountantDashboard);
