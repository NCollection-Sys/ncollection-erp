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
        NcSectionCard,
        NcBadge,
        NcQuickActionCard,
        NcChartWrapper,
        NcEmptyState,
        NcLoadingSkeleton,
    };

    noop() {
        // Playground quick-actions are inert.
    }
}

registry.category("actions").add("ncollection_branding.component_playground", NcComponentPlayground);
