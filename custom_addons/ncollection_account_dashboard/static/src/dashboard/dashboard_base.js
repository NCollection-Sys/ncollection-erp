/** @odoo-module **/

/**
 * Shared financial-dashboard base (F3-T01).
 *
 * The reusable OWL infrastructure the Finance / Accountant / Cash dashboards —
 * and later #56 (CEO) / #57 (department) — all build on. A subclass supplies
 * only its `serviceMethod` (the ncollection.account.dashboard.service payload
 * builder) and `title`; this base owns the fetch, the loading/empty/error
 * states, the Chart.js lifecycle, currency formatting and the trend arrow.
 *
 * The component performs NO financial computation: it renders whatever the
 * server payload contains. The trend delta is a pure presentation transform of
 * two values the service already produced (value vs previous).
 */

import {
    Component,
    onMounted,
    onPatched,
    onWillStart,
    onWillUnmount,
    useRef,
    useState,
} from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import {
    NcKpiCard,
    NcChartWrapper,
    NcEmptyState,
    NcLoadingSkeleton,
    ncChartSeries,
} from "@ncollection_branding/components/components";

export class NcFinancialDashboard extends Component {
    static template = "ncollection_account_dashboard.FinancialDashboard";
    static props = ["*"];
    static components = {
        NcKpiCard,
        NcChartWrapper,
        NcEmptyState,
        NcLoadingSkeleton,
    };

    // Subclasses override.
    get serviceMethod() {
        return null;
    }
    get title() {
        return "";
    }
    /**
     * Positional arguments for the service call. Empty by default, so Finance /
     * Accountant / Cash keep their existing no-argument contract untouched;
     * #56's CEO dashboard overrides it to pass a selected date range.
     */
    get fetchArgs() {
        return [];
    }

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.root = useRef("root");
        this.state = useState({ loading: true, error: false, payload: null });
        this._charts = [];
        this._chartsDirty = false;

        onWillStart(() => this.load());
        onMounted(() => this._mountCharts());
        // A reload replaces the payload, so OWL rebuilds every <canvas>. The old
        // Chart.js instances still point at the detached nodes and would leak
        // (and keep their resize listeners), so they are torn down and rebuilt —
        // but only on a patch that actually followed a fetch, not on every patch.
        onPatched(() => {
            if (this._chartsDirty) {
                this._chartsDirty = false;
                this._destroyCharts();
                this._mountCharts();
            }
        });
        onWillUnmount(() => this._destroyCharts());
    }

    /** Fetch (or re-fetch) the payload. Safe to call after the initial render. */
    async load() {
        this.state.loading = true;
        this.state.error = false;
        try {
            this.state.payload = await this.orm.call(
                "ncollection.account.dashboard.service",
                this.serviceMethod,
                this.fetchArgs
            );
        } catch {
            this.state.error = true;
        }
        this.state.loading = false;
        this._chartsDirty = true;
    }

    // ---- KPI presentation (no financial math — display transforms only) ---

    /** Trend arrow from the two service figures; null when no comparison. */
    trend(kpi) {
        const previous = kpi.previous;
        if (previous === undefined || previous === null) {
            return null;
        }
        const value = kpi.value ?? 0;
        const delta = value - previous;
        if (delta === 0) {
            // NcKpiCard renders any non-"down" dir as an up chip, so a "flat"
            // dir would show a misleading green ↑ 0.0%. No chip is truthful.
            return null;
        }
        const dir = delta > 0 ? "up" : "down";
        const pct = previous !== 0 ? (delta / Math.abs(previous)) * 100 : null;
        return { value: pct === null ? "—" : `${pct.toFixed(1)}%`, dir };
    }

    /** Render a KPI value using the payload's currency / percent unit. */
    formatValue(kpi) {
        const value = kpi.value ?? 0;
        if (kpi.unit === "percent") {
            return `${value.toFixed(1)}%`;
        }
        return this.formatAmount(value);
    }

    /** A bare number as tenant currency. */
    formatAmount(value) {
        const num = (value ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 });
        const currency = this.state.payload?.meta?.currency;
        if (!currency) {
            return num;
        }
        return currency.position === "before"
            ? `${currency.symbol} ${num}`
            : `${num} ${currency.symbol}`;
    }

    // ---- cross-domain panels (#56) ----------------------------------------

    /** Sub-template name for the header toolbar; null renders nothing. */
    get toolbarTemplate() {
        return null;
    }

    /**
     * Row width as a share of the panel's largest row.
     *
     * Scaled against the MAXIMUM, not the total. A funnel stage is not a slice
     * of a whole — stages overlap in time and a deal sits in exactly one — so
     * drawing them as percentages of a sum would invite reading the panel as a
     * composition breakdown, which it is not.
     */
    barStyle(panel, row) {
        const peak = Math.max(...panel.rows.map((r) => Math.abs(r.value ?? 0)), 0);
        const share = peak ? (Math.abs(row.value ?? 0) / peak) * 100 : 0;
        return `width: ${share.toFixed(1)}%`;
    }

    drillDownHint(panel) {
        return panel.drilldown ? _t("Open the underlying records") : "";
    }

    /**
     * Open the records behind one panel row.
     *
     * The payload names the target (`drilldown: {model, field}`) so the client
     * never has to know which model backs a panel. No access decision is taken
     * here: this opens an ordinary action, and the server applies the same ACLs
     * and record rules it would for any other list view. A user who cannot read
     * crm.lead gets Odoo's own error, not a dashboard-shaped bypass (Rule 4/7).
     */
    drillDown(panel, row) {
        const target = panel.drilldown;
        const value = target ? row[target.field] : undefined;
        // The null group arrives as id `false`. There is no record to open, and
        // a domain of `field = false` would silently list every unassigned
        // record — a different question than the one the user clicked on.
        if (!target || !value) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            name: `${panel.label}: ${row.label}`,
            res_model: target.model,
            views: [[false, "list"], [false, "form"]],
            domain: [[target.field, "=", value]],
        });
    }

    // ---- Chart.js lifecycle (dashboard owns the instances) ----------------

    _mountCharts() {
        const payload = this.state.payload;
        if (!payload || !this.root.el) {
            return;
        }
        const palette = ncChartSeries();
        for (const canvas of this.root.el.querySelectorAll("canvas[data-chart-key]")) {
            const chart = payload.charts.find((c) => c.key === canvas.dataset.chartKey);
            if (!chart) {
                continue;
            }
            const datasets = chart.series.map((s, i) => ({
                label: s.name,
                data: s.data,
                borderColor: palette[i % palette.length],
                backgroundColor: palette[i % palette.length],
                tension: 0.3,
            }));
            // eslint-disable-next-line no-undef
            this._charts.push(new Chart(canvas, {
                type: chart.type,
                data: { labels: chart.labels, datasets },
                options: { responsive: true, maintainAspectRatio: false },
            }));
        }
    }

    _destroyCharts() {
        this._charts.forEach((c) => c.destroy());
        this._charts = [];
    }
}
