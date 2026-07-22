/** @odoo-module **/

/**
 * NCollection shared OWL component library (UI-T02 / #129).
 *
 * The reusable presentation layer implementing the design system frozen in
 * UI-T01 (#128). Every component is PURE PRESENTATION — no ORM/RPC, no model
 * access — so it is layer-agnostic (tenant dashboards in ncollection_core and
 * the platform SaaS-admin dashboard may both consume it without breaching the
 * two-layer separation rule) and styled ONLY through the --nc-* design tokens
 * (components.scss carries zero hard-coded colours or radii — enforced by
 * tests/test_component_library.py).
 *
 * These seed from the P1-T17 dashboard's inline KPI/chart cards (the "#18
 * widgets seed the library" note on #129); migrating the dashboard itself to
 * consume them is deliberately out of scope here (accepted, documented debt).
 */

import { Component } from "@odoo/owl";

/** Read a design token off :root, with a fallback. Used by chart consumers to
 *  pull the frozen --nc-chart-* series palette (§16) at render time so charts
 *  inherit tenant/theme values. */
export function ncToken(name, fallback = "") {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (value || "").trim() || fallback;
}

/** The categorical chart series palette (UI-T01 --nc-chart-1..5). */
export function ncChartSeries() {
    return [
        ncToken("--nc-chart-1", "#1F5F8F"),
        ncToken("--nc-chart-2", "#8145CD"),
        ncToken("--nc-chart-3", "#CD45A2"),
        ncToken("--nc-chart-4", "#FB2B76"),
        ncToken("--nc-chart-5", "#13CD3C"),
    ];
}

/** KPI tile: label, big value, optional trend chip + sub-text, optional accent
 *  bar. Seeded from ncollection_core.DashboardKpiCard. */
export class NcKpiCard extends Component {
    static template = "ncollection_branding.NcKpiCard";
    static props = {
        label: { type: String },
        value: { type: [String, Number] },
        trend: { type: Object, optional: true },   // { value: String, dir: "up"|"down" }
        sub: { type: String, optional: true },
        accent: { type: Boolean, optional: true },  // show the primary accent bar
    };
    static defaultProps = { accent: true };

    get trendClass() {
        const dir = this.props.trend && this.props.trend.dir;
        return dir === "down" ? "nc-trend nc-trend--down" : "nc-trend nc-trend--up";
    }
}

/** Titled surface card with a default content slot. The generic container the
 *  other cards and dashboards compose inside. */
export class NcSectionCard extends Component {
    static template = "ncollection_branding.NcSectionCard";
    static props = {
        title: { type: String, optional: true },
        slots: { type: Object, optional: true },
    };
}

/** Status badge. `variant` maps to a semantic --nc-status-* token (meaning is
 *  fixed by the design system, never a brand colour). */
export class NcBadge extends Component {
    static template = "ncollection_branding.NcBadge";
    static props = {
        label: { type: String },
        variant: {
            type: String,
            optional: true,
            validate: (v) => ["success", "error", "warning", "info", "neutral"].includes(v),
        },
    };
    static defaultProps = { variant: "neutral" };
}

/** Quick-action card/button — an icon + label calling back on click. Seeded
 *  from the dashboard's quick-action buttons. */
export class NcQuickActionCard extends Component {
    static template = "ncollection_branding.NcQuickActionCard";
    static props = {
        label: { type: String },
        icon: { type: String, optional: true },   // a fa-* class name
        onClick: { type: Function, optional: true },
    };

    onClick() {
        if (this.props.onClick) {
            this.props.onClick();
        }
    }
}

/** Presentational chart wrapper: a section card with a bounded canvas area the
 *  consumer draws into (Chart.js is owned by the consumer). Exposes the series
 *  palette via the exported ncChartSeries() helper; this component only frames
 *  the chart and never draws, keeping the library charting-engine agnostic. */
export class NcChartWrapper extends Component {
    static template = "ncollection_branding.NcChartWrapper";
    static props = {
        title: { type: String, optional: true },
        slots: { type: Object, optional: true },
    };
}

/** Empty state: icon + title + description + optional action slot. Seeded from
 *  the dashboard's "no widgets" status block. */
export class NcEmptyState extends Component {
    static template = "ncollection_branding.NcEmptyState";
    static props = {
        title: { type: String },
        description: { type: String, optional: true },
        icon: { type: String, optional: true },   // a fa-* class name
        slots: { type: Object, optional: true },
    };
    static defaultProps = { icon: "fa-inbox" };
}

/** Loading skeleton: shimmer placeholder blocks. Honours prefers-reduced-motion
 *  (the shimmer is disabled there — §23 accessibility). */
export class NcLoadingSkeleton extends Component {
    static template = "ncollection_branding.NcLoadingSkeleton";
    static props = {
        lines: { type: Number, optional: true },
        variant: {
            type: String,
            optional: true,
            validate: (v) => ["text", "card"].includes(v),
        },
    };
    static defaultProps = { lines: 3, variant: "text" };

    get range() {
        return Array.from({ length: Math.max(1, this.props.lines) }, (_, i) => i);
    }
}
