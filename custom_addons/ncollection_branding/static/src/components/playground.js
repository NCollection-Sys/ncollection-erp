/** @odoo-module **/

/**
 * Component library playground (UI-T02 / #129).
 *
 * A dev/QA surface that renders every library component with sample props, so
 * the library is visually reviewable and the "components render from a demo
 * playground page" acceptance is exercised end-to-end (an HttpCase loads this
 * client action). Registered as a client action and surfaced on an
 * admin-only menu — it is not a tenant-user feature.
 */

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import {
    NcKpiCard,
    NcStatementCard,
    NcSectionCard,
    NcBadge,
    NcQuickActionCard,
    NcChartWrapper,
    NcEmptyState,
    NcLoadingSkeleton,
} from "./components";

export class NcComponentPlayground extends Component {
    static template = "ncollection_branding.ComponentPlayground";
    static props = ["*"];
    static components = {
        NcKpiCard,
        NcStatementCard,
        NcSectionCard,
        NcBadge,
        NcQuickActionCard,
        NcChartWrapper,
        NcEmptyState,
        NcLoadingSkeleton,
    };

    get sampleRows() {
        return [
            { key: "a", label: "ASSETS", display: "AED 0.00", level: 0, amount: 0 },
            { key: "b", label: "Current Assets", display: "AED 53,816.70", level: 1, amount: 1, drill: true },
            { key: "c", label: "1010 Bank", display: "AED 40,000.00", level: 2, amount: 1, drill: true },
            { key: "d", label: "1100 Receivable", display: "AED 13,816.70", level: 2, amount: 1, drill: true },
            { key: "e", label: "TOTAL ASSETS", display: "AED 65,816.70", level: 0, amount: 1, drill: true },
            { key: "f", label: "Net result", display: "-AED 1,250.00", level: 0, amount: -1, drill: true },
        ];
    }

    noop() {
        // Playground quick-actions are inert.
    }
}

registry.category("actions").add("ncollection_branding.component_playground", NcComponentPlayground);
