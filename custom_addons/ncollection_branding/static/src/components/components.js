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

import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";

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

/**
 * THE KPI card of the whole product (one design, every module): a compact tile with an optional icon chip,
 * a small label, the figure, and a quiet line for trend / context / period.
 *
 *  - `state`: "loaded" (default) | "loading" | "empty" | "noaccess" | "error". A figure of 0 is "loaded" and is
 *    simply drawn muted (zero is an answer, not a failure).
 *  - `tone`: "neutral" | "positive" | "negative" | "warning" | "info": SEMANTIC only (it colours the icon chip
 *    and the figure; never decoration).
 *  - `onClick`: makes the whole card one real <button> with a chevron, hover and a keyboard focus ring. Give it
 *    only when there is a meaningful destination; a card without it is a plain, non-interactive tile.
 *  - `period` / `sub` / `trend`: context. `trend` is { value: String, dir: "up"|"down" }.
 * Pure presentation: no ORM/RPC. Styled by components.scss through --nc-* tokens only.
 */
export class NcKpiCard extends Component {
    static template = "ncollection_branding.NcKpiCard";
    static props = {
        label: { type: String },
        value: { type: [String, Number], optional: true },
        trend: { type: Object, optional: true },
        sub: { type: String, optional: true },
        period: { type: String, optional: true },
        icon: { type: String, optional: true },     // a fa-* class name
        tone: {
            type: String,
            optional: true,
            validate: (v) => ["neutral", "positive", "negative", "warning", "info"].includes(v),
        },
        state: {
            type: String,
            optional: true,
            validate: (v) => ["loaded", "loading", "empty", "noaccess", "error"].includes(v),
        },
        accent: { type: Boolean, optional: true },  // legacy: a primary accent bar (off by default)
        onClick: { type: Function, optional: true },
        title: { type: String, optional: true },
    };
    static defaultProps = { accent: false, state: "loaded", tone: "neutral" };

    get trendClass() {
        const dir = this.props.trend && this.props.trend.dir;
        return dir === "down" ? "nc-trend nc-trend--down" : "nc-trend nc-trend--up";
    }

    get classes() {
        return {
            "nc-card": true,
            "nc-card--interactive": !!this.props.onClick,
            "nc-kpi-card--accent": this.props.accent,
            [`nc-kpi-card--${this.props.tone}`]: this.props.tone !== "neutral",
            [`nc-kpi-card--${this.props.state}`]: this.props.state !== "loaded",
            "nc-kpi-card--zero": this.isZero,
        };
    }

    get isZero() {
        const v = this.props.value;
        return this.props.state === "loaded" && (v === 0 || /^[^\d]*0([.,]0+)?[^\d]*$/.test(String(v ?? "")));
    }

    onClick() {
        if (this.props.onClick) {
            this.props.onClick();
        }
    }
}

/**
 * A financial statement as a card (Balance Sheet, Profit & Loss, any report summary): a title, a period, and
 * rows of label + amount. Rows come pre-formatted; this component only lays them out.
 *   row: { key, label, display, level (0|1|2), amount (number, for the sign), drill (bool) }
 * Level 0 = section / total (bold), 1 = group, 2 = account. Level-2 rows are collapsed under their group and a
 * group with accounts gets a toggle. A row with `drill` shows its amount as a button that calls
 * `onRowClick(row)`: the caller decides what opens (the existing report drill-down); this component never does
 * a fetch. `onOpen` is the "open the full report" link in the header.
 */
export class NcStatementCard extends Component {
    static template = "ncollection_branding.NcStatementCard";
    static props = {
        title: { type: String },
        period: { type: String, optional: true },
        rows: { type: Array },
        empty: { type: Boolean, optional: true },
        emptyText: { type: String, optional: true },
        openLabel: { type: String, optional: true },
        onOpen: { type: Function, optional: true },
        onRowClick: { type: Function, optional: true },
        expandLabel: { type: String, optional: true },
    };

    setup() {
        this.state = useState({ open: {} });
    }

    get openLabelText() {
        return this.props.openLabel || _t("Open full report");
    }

    get expandLabelText() {
        return this.props.expandLabel || _t("Show accounts");
    }

    /** The rows to draw: levels 0 and 1 always, level-2 rows only under an expanded group (the nearest
     *  preceding level-1 row). */
    get visibleRows() {
        const out = [];
        let group = -1;
        this.props.rows.forEach((row, index) => {
            if (row.level === 1) {
                group = index;
            } else if (row.level === 0) {
                group = -1;
            }
            const hasKids = row.level === 1 && this.props.rows[index + 1]?.level === 2;
            if (row.level >= 2 && !this.state.open[group]) {
                return;
            }
            out.push({ row, index, hasKids, expanded: !!this.state.open[index] });
        });
        return out;
    }

    toggle(index) {
        this.state.open[index] = !this.state.open[index];
    }

    onRowClick(row) {
        if (this.props.onRowClick) {
            this.props.onRowClick(row);
        }
    }

    rowClass(item) {
        const { row } = item;
        return `nc-statement-row nc-statement-row--l${row.level}`
            + (row.amount < 0 ? " nc-statement-row--negative" : "");
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
