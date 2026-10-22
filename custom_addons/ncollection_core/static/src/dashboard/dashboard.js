/** @odoo-module **/

/**
 * Summary: the company's executive home (P1-T17, extended).
 *
 * This component deliberately performs NO filtering of its own. The server
 * decides which widgets the user may see — by role, and by whether the plan
 * installs and licenses the underlying app — and returns only those
 * (ncollection.dashboard.data.get_dashboard_payload). Filtering here as well
 * would imply the client is a security boundary, which it is not (Standing
 * Rule 4).
 *
 * It also does no accounting. Statement rows and the Net Profit / Expenses figures are the report engine's own
 * rows; a click opens the source journal items through the report service's drill (`nc_report_drill`) or, for
 * the operational cards, `drill_kpi`: both server-side, both access-checked, neither takes a domain from here.
 */

import { Component, onMounted, onPatched, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";
import {
    NcKpiCard,
    NcQuickActionCard,
    NcStatementCard,
} from "@ncollection_branding/components/components";

/** Read a brand token so charts inherit tenant colours (P1-T16-ready). */
function brandColor(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (value || "").trim() || fallback;
}

// Server icon keys -> Font Awesome 4.7 (the set Odoo ships).
const ICONS = {
    sales: "fa-line-chart",
    invoicing: "fa-file-text-o",
    wallet: "fa-money",
    activity: "fa-clock-o",
    check: "fa-check-circle",
    purchase: "fa-shopping-cart",
    inventory: "fa-cubes",
    chart: "fa-pie-chart",
};
const QUICK_ICONS = {
    new_quotation: "fa-plus",
    new_invoice: "fa-file-text-o",
    new_po: "fa-shopping-cart",
};

export class NcDashboard extends Component {
    static template = "ncollection_core.Dashboard";
    static props = ["*"];
    static components = { NcKpiCard, NcQuickActionCard, NcStatementCard };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.chartsRoot = useRef("charts");
        this._charts = [];
        this.texts = {
            statementEmpty: _t("No posted entries in this period yet."),
            openReport: _t("Open full report"),
            showAccounts: _t("Show accounts"),
        };

        this.state = useState({
            loading: true,
            failed: false,
            refreshing: false,
            widgets: [],
            actions: [],
            meta: {},
            from: "",
            to: "",
            periodError: "",
        });
        this._seq = 0; // a late answer to an older request never overwrites a newer one
        this._timer = null;
        this._pendingCharts = false;

        onWillStart(async () => {
            const saved = this._savedPeriod();
            await this.load(saved && saved.from, saved && saved.to, true);
        });

        // After a refresh the canvases are new DOM nodes: draw them once the page has been patched.
        onPatched(() => {
            if (this._pendingCharts) {
                this._pendingCharts = false;
                this._mountCharts();
            }
        });
        onWillUnmount(() => clearTimeout(this._timer));

        // Data is fetched in onWillStart, so by the time we mount the canvases
        // are already in the DOM with their widget data.
        onMounted(() => this._mountCharts());

        // Chart.js instances hold canvases and listeners; drop them explicitly.
        onWillUnmount(() => this._destroyCharts());
    }

    // -- the period (From / To) ------------------------------------------------------------------------
    // The server owns what each figure means by a period (period total, as of To, trend ending at To, current
    // state); this only asks for a window and shows it. Nothing here widens what the user may see.

    get periodKey() {
        return `nc_summary_period:${user.activeCompany.id}`;
    }

    _savedPeriod() {
        try {
            const saved = JSON.parse(sessionStorage.getItem(this.periodKey) || "null");
            return saved && saved.from && saved.to && saved.from <= saved.to ? saved : null;
        } catch {
            return null; // storage blocked: the default period, no harm done
        }
    }

    _savePeriod(from, to) {
        try {
            if (from && to) {
                sessionStorage.setItem(this.periodKey, JSON.stringify({ from, to }));
            } else {
                sessionStorage.removeItem(this.periodKey);
            }
        } catch {
            /* a convenience only */
        }
    }

    async load(from, to, initial = false) {
        const seq = ++this._seq;
        if (!initial) {
            this.state.refreshing = true;
        }
        try {
            // One round-trip for the whole page (acceptance: loads under 2s).
            const payload = await this.orm.call(
                "ncollection.dashboard.data",
                "get_dashboard_payload",
                [],
                from && to ? { date_from: from, date_to: to } : {}
            );
            if (seq !== this._seq) {
                return;
            }
            this._destroyCharts();
            this.state.widgets = payload.widgets || [];
            this.state.actions = payload.actions || [];
            this.state.meta = payload.meta || {};
            const period = this.state.meta.period || {};
            this.state.from = period.date_from || "";
            this.state.to = period.date_to || "";
            this.state.periodError = "";
            this._pendingCharts = !initial;
        } catch (error) {
            if (seq !== this._seq) {
                return;
            }
            if (initial) {
                // Never leave the landing page blank and unexplained.
                this.state.failed = true;
            } else {
                const data = error && error.data;
                this.notification.add((data && data.message) || _t("The summary could not be refreshed."), {
                    type: "danger",
                });
            }
        } finally {
            if (seq === this._seq) {
                this.state.loading = false;
                this.state.refreshing = false;
            }
        }
    }

    get isDefaultPeriod() {
        return !this.state.meta.period || this.state.meta.period.is_default;
    }

    _periodProblem() {
        const { from, to } = this.state;
        if (!from || !to) {
            return _t("Choose both the From date and the To date.");
        }
        if (from > to) {
            return _t("The From date cannot be after the To date.");
        }
        return "";
    }

    onPeriodChange(which, ev) {
        this.state[which] = ev.target.value;
        this.state.periodError = this._periodProblem();
        clearTimeout(this._timer);
        if (this.state.periodError) {
            return; // nothing is asked of the server for an unusable range
        }
        const period = this.state.meta.period || {};
        if (this.state.from === period.date_from && this.state.to === period.date_to) {
            return; // already showing it: no repeated call
        }
        this._timer = setTimeout(() => this.applyPeriod(), 300);
    }

    async applyPeriod() {
        this._savePeriod(this.state.from, this.state.to);
        await this.load(this.state.from, this.state.to);
    }

    async resetPeriod() {
        clearTimeout(this._timer);
        this._savePeriod(null, null);
        await this.load(null, null);
    }

    get firstName() {
        return (this.state.meta.user_name || "").split(" ")[0];
    }

    get kpis() {
        return this.state.widgets.filter((w) => w.type === "kpi");
    }

    get statements() {
        return this.state.widgets.filter((w) => w.type === "statement");
    }

    get charts() {
        return this.state.widgets.filter((w) => w.type === "chart");
    }

    iconFor(widget) {
        return ICONS[widget.icon] || ICONS.activity;
    }

    quickIcon(key) {
        return QUICK_ICONS[key];
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

    /** Full-precision amount for a statement row (a statement is read to the cent, not compacted). */
    formatMoney(value) {
        const symbol = this.state.meta.currency || "";
        const text = new Intl.NumberFormat(undefined, {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        }).format(value ?? 0);
        return symbol ? `${symbol} ${text}` : text;
    }

    /** The trend chip for the shared KPI card; undefined when there is no prior period to compare against. */
    trendFor(widget) {
        if (widget.trend === null || widget.trend === undefined) {
            return undefined;
        }
        const rounded = Math.round(widget.trend);
        return {
            value: `${rounded >= 0 ? "▲" : "▼"} ${Math.abs(rounded)}%`,
            dir: rounded >= 0 ? "up" : "down",
        };
    }

    /** The statement's rows for the shared statement card. A row is clickable only when the server gave it accounts. */
    statementRows(widget) {
        return (widget.rows || []).map((row, index) => ({
            key: row.key || String(index),
            label: row.label,
            display: this.formatMoney(row.value),
            level: row.level,
            amount: row.value,
            drill: !!(widget.wizard && row.account_ids && row.account_ids.length),
            account_ids: row.account_ids || [],
        }));
    }

    /** Open an action produced by a server drill; a refusal is a notice, never a blank page. */
    async _open(fetchAction) {
        try {
            await this.action.doAction(await fetchAction());
        } catch {
            this.notification.add(_t("This figure cannot be opened."), { type: "warning" });
        }
    }

    drill(widget) {
        const drill = widget.drill;
        return this._open(() =>
            drill.kind === "accounts"
                ? this.orm.call("ncollection.report.service", "nc_report_drill", [drill.wizard.model, drill.wizard.id], {
                      account_ids: drill.account_ids,
                      label: drill.label,
                  })
                : this.orm.call("ncollection.dashboard.data", "drill_kpi", [widget.key])
        );
    }

    drillStatement(widget, row) {
        return this._open(() =>
            this.orm.call("ncollection.report.service", "nc_report_drill", [widget.wizard.model, widget.wizard.id], {
                account_ids: row.account_ids,
                label: row.label,
            })
        );
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
