/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { SpreadsheetDashboardAction } from "@spreadsheet_dashboard/bundle/dashboard_action/dashboard_action";
import { NcDashboardExportButton } from "./export_button";

patch(SpreadsheetDashboardAction, {
    components: { ...SpreadsheetDashboardAction.components, NcDashboardExportButton },
});
