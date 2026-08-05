/** @odoo-module **/

import { registry } from "@web/core/registry";
import { NcFinancialDashboard } from "./dashboard_base";
import { _t } from "@web/core/l10n/translation";

/** Sales department dashboard (#57): deal-size KPI + pipeline + leaderboard.
 *  Reuses the shared base verbatim — only the payload endpoint differs. */
export class SalesDashboard extends NcFinancialDashboard {
    get serviceMethod() {
        return "get_sales_dashboard";
    }
    get title() {
        return _t("Sales Dashboard");
    }
}

registry.category("actions").add("ncollection_account_dashboard.sales", SalesDashboard);
