/** @odoo-module **/

/**
 * CEO dashboard (#56 / P4-T03).
 *
 * Adds three things to the shared base: a date-range selector, the cross-domain
 * panels' drill-down (inherited — the payload names its own targets), and a
 * print/PDF export.
 *
 * It performs NO computation of any kind. The range is handed to the server
 * untouched and every figure comes back from the F2-T08 executive services, so
 * the "zero financial computation" boundary this module is built around still
 * holds on the client as well as the server.
 */

import { registry } from "@web/core/registry";
import { useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { NcFinancialDashboard } from "./dashboard_base";

export class CeoDashboard extends NcFinancialDashboard {
    get serviceMethod() {
        return "get_ceo_dashboard";
    }
    get title() {
        return _t("CEO Dashboard");
    }
    get toolbarTemplate() {
        return "ncollection_account_dashboard.CeoToolbar";
    }

    /**
     * Both dates or neither. The server ignores a half-specified range, and
     * sending one anyway would leave the header showing a range the figures do
     * not actually cover.
     */
    get fetchArgs() {
        const { from, to } = this.range;
        return from && to ? [from, to] : [];
    }

    setup() {
        super.setup();
        // Not seeded from the payload: until the user picks a range the server
        // owns the default period (YTD), and copying it into the inputs would
        // present a server default as a user choice.
        this.range = useState({ from: "", to: "" });
    }

    get rangeIsPartial() {
        return Boolean(this.range.from) !== Boolean(this.range.to);
    }

    /** Invalid only when inverted — a partial range is "not yet", not "wrong". */
    get rangeIsInvalid() {
        return Boolean(this.range.from && this.range.to && this.range.from > this.range.to);
    }

    async applyRange() {
        if (this.rangeIsPartial || this.rangeIsInvalid) {
            return;
        }
        await this.load();
    }

    async resetRange() {
        this.range.from = "";
        this.range.to = "";
        await this.load();
    }

    /**
     * Export via the browser's print pipeline, scoped by the print stylesheet.
     *
     * Deliberately NOT a server-rendered QWeb report. A QWeb report would have
     * to re-render every figure and re-draw the charts server-side (wkhtmltopdf
     * does not execute the Chart.js that draws them), which means a second
     * rendering path that can silently disagree with the screen — the class of
     * divergence this module's provenance tests exist to prevent. Printing the
     * rendered DOM guarantees the PDF shows exactly what the user saw.
     */
    exportPdf() {
        window.print();
    }
}

registry.category("actions").add("ncollection_account_dashboard.ceo", CeoDashboard);
