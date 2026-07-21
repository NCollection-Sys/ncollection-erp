/** @odoo-module **/

/**
 * Customer Workspace Dashboard (P1-T17).
 *
 * Ported from the reviewed prototype at demo/src/pages/Dashboard.tsx, which
 * demo/README.md designates as the design reference for this screen.
 *
 * This component deliberately performs NO filtering of its own. The server
 * decides which widgets the user may see and returns only those
 * (ncollection.dashboard.data.get_dashboard_payload). Filtering here as well
 * would imply the client is a security boundary, which it is not — Standing
 * Rule 4: UI hiding alone is not security.
 */

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class NcDashboard extends Component {
    static template = "ncollection_core.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.state = useState({
            loading: true,
            failed: false,
            widgets: [],
            meta: {},
        });

        onWillStart(async () => {
            try {
                // One round-trip for the whole page (acceptance: loads under 2s).
                const payload = await this.orm.call(
                    "ncollection.dashboard.data",
                    "get_dashboard_payload",
                    []
                );
                this.state.widgets = payload.widgets || [];
                this.state.meta = payload.meta || {};
            } catch {
                // Never leave the landing page blank and unexplained.
                this.state.failed = true;
            } finally {
                this.state.loading = false;
            }
        });
    }

    /** First name only, for the greeting — matches the demo's header. */
    get firstName() {
        return (this.state.meta.user_name || "").split(" ")[0];
    }
}

registry.category("actions").add("ncollection_core.dashboard", NcDashboard);
