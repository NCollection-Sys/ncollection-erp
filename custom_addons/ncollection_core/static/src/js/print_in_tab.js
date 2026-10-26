/** @odoo-module */
/**
 * Print opens the document's PDF in a new tab instead of downloading it.
 *
 * The server's Print (nc.document.print.mixin.action_nc_print, account.move.action_print_pdf) returns the SAME report
 * action View/Preview renders, marked ``nc_print_in_tab`` in its context. This handler runs before Odoo's own download
 * handler and opens /report/pdf/<report>/<ids> with that context: Odoo's report controller, the same report, the same
 * renderer and template, served inline. The browser's PDF viewer prints it. Every other report keeps Odoo's behaviour.
 */
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";

async function printInTab(action) {
    const context = action.context || {};
    if (action.report_type !== "qweb-pdf" || !context.nc_print_in_tab) {
        return false;
    }
    const ids = context.active_ids || [];
    const query = new URLSearchParams({ context: JSON.stringify(context) });
    browser.open(`/report/pdf/${action.report_name}/${ids.join(",")}?${query}`, "_blank");
    return true;
}

registry.category("ir.actions.report handlers").add("nc_print_in_tab", printInTab, { sequence: 1 });
