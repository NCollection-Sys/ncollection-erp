/** @odoo-module **/
/*
 * The Reports hub: category cards listing what THIS user may open.
 *
 * The catalog comes from `ncollection.report.service.nc_report_catalog()`,
 * which already applied menu groups, plan licensing and model access, so a card
 * never leads to an access error and nothing here lists reports itself. Opening
 * a report is a plain action call on the report's own menu action.
 */
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class NcReportHub extends Component {
    static template = "ncollection_account_reports.ReportHub";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ loaded: false, categories: [], query: "" });
        onWillStart(async () => {
            const data = await this.orm.call("ncollection.report.service", "nc_report_catalog", []);
            this.state.categories = data.categories;
            this.state.loaded = true;
        });
    }

    get visibleCategories() {
        const q = this.state.query.trim().toLowerCase();
        if (!q) {
            return this.state.categories;
        }
        return this.state.categories
            .map((c) => ({ ...c, reports: c.reports.filter((r) => r.name.toLowerCase().includes(q)) }))
            .filter((c) => c.reports.length);
    }

    openReport(report) {
        return this.action.doAction(report.action_id);
    }
}

registry.category("actions").add("ncollection_account_reports.report_hub", NcReportHub);
