/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { SpreadsheetDashboardAction } from "@spreadsheet_dashboard/bundle/dashboard_action/dashboard_action";
import { NcDashboardExportButton } from "./export_button";
import { NcDashboardContext } from "./dashboard_context";

patch(SpreadsheetDashboardAction, {
    components: { ...SpreadsheetDashboardAction.components, NcDashboardExportButton, NcDashboardContext },
});
