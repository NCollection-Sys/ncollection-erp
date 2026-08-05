/** @odoo-module **/

import { registry } from "@web/core/registry";
import { NcFinancialDashboard } from "./dashboard_base";
import { _t } from "@web/core/l10n/translation";

/** Warehouse department dashboard (#57): inventory-turnover KPI + valuation and
 *  movement panels. */
export class WarehouseDashboard extends NcFinancialDashboard {
    get serviceMethod() {
        return "get_warehouse_dashboard";
    }
    get title() {
        return _t("Warehouse Dashboard");
    }
}

registry.category("actions").add("ncollection_account_dashboard.warehouse", WarehouseDashboard);
