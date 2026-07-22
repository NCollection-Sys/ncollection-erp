/** @odoo-module **/

/**
 * Customer Workspace Dashboard (P1-T17).
 *
 * Ported from the reviewed prototype at demo/src/pages/Dashboard.tsx, which
 * demo/README.md designates as the design reference for this screen.
 *
 * This component deliberately performs NO filtering of its own. The server
 * decides which widgets the user may see — by role, and by whether the plan
 * installs and licenses the underlying app — and returns only those
 * (ncollection.dashboard.data.get_dashboard_payload). Filtering here as well
 * would imply the client is a security boundary, which it is not (Standing
 * Rule 4).
 */

import { Component, onMounted, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/** Read a brand token so charts inherit tenant colours (P1-T16-ready). */
function brandColor(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (value || "").trim() || fallback;
}

export class NcDashboard extends Component {
    static template = "ncollection_core.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.chartsRoot = useRef("charts");
        this._charts = [];

        this.state = useState({
            loading: true,
            failed: false,
            widgets: [],
            actions: [],
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
                this.state.actions = payload.actions || [];
                this.state.meta = payload.meta || {};
            } catch {
                // Never leave the landing page blank and unexplained.
                this.state.failed = true;
            } finally {
                this.state.loading = false;
            }
        });

        // Data is fetched in onWillStart, so by the time we mount the canvases
        // are already in the DOM with their widget data.
        onMounted(() => this._mountCharts());

        // Chart.js instances hold canvases and listeners; drop them explicitly.
        onWillUnmount(() => this._destroyCharts());
    }

    get firstName() {
        return (this.state.meta.user_name || "").split(" ")[0];
    }

    get kpis() {
        return this.state.widgets.filter((w) => w.type === "kpi");
    }

    get charts() {
        return this.state.widgets.filter((w) => w.type === "chart");
    }

    /** Currency/integer formatting, mirroring the prototype's compact tiles. */
    formatValue(widget) {
        const value = widget.value ?? 0;
        if (widget.format === "currency") {
            const symbol = this.state.meta.currency || "";
            const compact = new Intl.NumberFormat(undefined, {
                notation: "compact",
                maximumFractionDigits: 1,
            }).format(value);
            return symbol ? `${symbol} ${compact}` : compact;
        }
        return new Intl.NumberFormat().format(value);
    }

    /** Trend chip text; null when there is no prior period to compare against. */
    formatTrend(widget) {
        if (widget.trend === null || widget.trend === undefined) {
            return null;
        }
        const rounded = Math.round(widget.trend);
        return `${rounded >= 0 ? "▲" : "▼"} ${Math.abs(rounded)}%`;
    }

    trendClass(widget) {
        return (widget.trend ?? 0) >= 0 ? "nc-trend--up" : "nc-trend--down";
    }

    _destroyCharts() {
        this._charts.forEach((chart) => chart.destroy());
        this._charts = [];
    }

    /**
     * Draw every chart canvas, pairing each to its widget by data-key.
     *
     * Chart.js ships with Odoo (web/static/lib/Chart/Chart.js). If it is
     * somehow unavailable we skip silently rather than break the whole landing
     * page for a decorative tile.
     */
    _mountCharts() {
        const root = this.chartsRoot.el;
        if (!root || !window.Chart) {
            return;
        }
        for (const canvas of root.querySelectorAll("canvas[data-key]")) {
            const widget = this.charts.find((w) => w.key === canvas.dataset.key);
            if (widget) {
                this._renderChart(canvas, widget);
            }
        }
    }

    _renderChart(canvas, widget) {
        const primary = brandColor("--nc-color-primary", "#1f5f8f");
        const secondary = brandColor("--nc-color-secondary", "#2d7ab7");
        const isLine = widget.chart === "line";

        this._charts.push(
            new window.Chart(canvas.getContext("2d"), {
                type: isLine ? "line" : "bar",
                data: {
                    labels: widget.labels || [],
                    datasets: [
                        {
                            label: widget.label,
                            data: widget.data || [],
                            borderColor: primary,
                            backgroundColor: isLine ? "transparent" : secondary,
                            borderWidth: isLine ? 2 : 0,
                            tension: 0.3,
                            pointRadius: 3,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: { y: { beginAtZero: true } },
                },
            })
        );
    }

    onQuickAction(actionId) {
        this.action.doAction(actionId);
    }
}

registry.category("actions").add("ncollection_core.dashboard", NcDashboard);
