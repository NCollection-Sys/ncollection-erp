/** @odoo-module **/

import { registry } from "@web/core/registry";
import { NcFinancialDashboard } from "./dashboard_base";
import { _t } from "@web/core/l10n/translation";

/** Cash dashboard — cash / receivables / payables position + cash trend. */
export class CashDashboard extends NcFinancialDashboard {
    get serviceMethod() {
        return "get_cash_dashboard";
    }
    get title() {
        return _t("Cash Dashboard");
    }
}

registry.category("actions").add("ncollection_account_dashboard.cash", CashDashboard);
