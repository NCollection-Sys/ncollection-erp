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

import { Component, onMounted, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import {
    NcKpiCard,
    NcSectionCard,
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
        NcSectionCard,
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

    setup() {
        this.orm = useService("orm");
        this.root = useRef("root");
        this.state = useState({ loading: true, error: false, payload: null });
        this._charts = [];

        onWillStart(async () => {
            try {
                this.state.payload = await this.orm.call(
                    "ncollection.account.dashboard.service",
                    this.serviceMethod,
                    []
                );
            } catch {
                this.state.error = true;
            }
            this.state.loading = false;
        });
        onMounted(() => this._mountCharts());
        onWillUnmount(() => this._destroyCharts());
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
        const dir = delta > 0 ? "up" : delta < 0 ? "down" : "flat";
        const pct = previous !== 0 ? (delta / Math.abs(previous)) * 100 : null;
        return { value: pct === null ? "—" : `${pct.toFixed(1)}%`, dir };
    }

    /** Render a KPI value using the payload's currency / percent unit. */
    formatValue(kpi) {
        const value = kpi.value ?? 0;
        if (kpi.unit === "percent") {
            return `${value.toFixed(1)}%`;
        }
        const num = value.toLocaleString(undefined, { maximumFractionDigits: 0 });
        const currency = this.state.payload?.meta?.currency;
        if (!currency) {
            return num;
        }
        return currency.position === "before"
            ? `${currency.symbol} ${num}`
            : `${num} ${currency.symbol}`;
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
