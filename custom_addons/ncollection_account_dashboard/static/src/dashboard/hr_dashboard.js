/** @odoo-module **/

import { registry } from "@web/core/registry";
import { NcFinancialDashboard } from "./dashboard_base";
import { _t } from "@web/core/l10n/translation";

/** HR department dashboard (#57): turnover KPI + headcount + leave panels. */
export class HrDashboard extends NcFinancialDashboard {
    get serviceMethod() {
        return "get_hr_dashboard";
    }
    get title() {
        return _t("HR Dashboard");
    }
}

registry.category("actions").add("ncollection_account_dashboard.hr", HrDashboard);
