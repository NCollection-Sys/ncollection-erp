/** @odoo-module **/
import { Component, onWillUpdateProps, onWillStart, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

/**
 * A band above the dashboard: which company and currency the figures are in, a plain warning when the page shows Odoo's
 * built-in SAMPLE data, and what the cards mean (only for dashboards whose formulas were checked).
 */
export class NcDashboardContext extends Component {
    static template = "ncollection_dashboard_export.Context";
    static props = { dashboardId: { type: [Number, Boolean], optional: true }, isSample: { type: Boolean, optional: true } };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ info: null, open: false });
        onWillStart(() => this.load(this.props.dashboardId));
        onWillUpdateProps((next) => this.load(next.dashboardId));
    }

    get sampleTitle() {
        return _t("Sample data.");
    }

    get sampleText() {
        return _t(
            "These figures are Odoo's illustrative demo numbers, not your company's records (the currency shown on the sample cards is part of the sample). Real figures appear once this company has data."
        );
    }

    get amountsIn() {
        return _t("Amounts in");
    }

    get mixedText() {
        return _t(
            "Several companies with different currencies are selected. Odoo adds their figures together without converting them, so these totals are not in one currency. Select a single company to read them."
        );
    }

    get aboutLabel() {
        return _t("About these figures");
    }

    async load(id) {
        this.state.info = null;
        if (!id) {
            return;
        }
        try {
            this.state.info = await this.orm.call("spreadsheet.dashboard", "nc_dashboard_context", [id]);
        } catch {
            this.state.info = null;
        }
    }
}
