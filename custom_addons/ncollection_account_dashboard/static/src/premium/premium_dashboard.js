/** @odoo-module **/
import { Component, onMounted, onPatched, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { ncChartSeries } from "@ncollection_branding/components/components";

const pad = (n) => String(n).padStart(2, "0");
const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

/**
 * NCollection Sales / Invoicing analysis. It renders what the server payload says and computes nothing financial:
 * every amount is already in the active company's currency, and the currency used to print it comes from the payload.
 */
export class NcPremiumDashboard extends Component {
    static template = "ncollection_account_dashboard.PremiumDashboard";
    static props = ["*"];

    get kind() {
        return "";
    }
    get title() {
        return "";
    }

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.root = useRef("root");
        this.charts = [];
        this.state = useState({
            loading: true, error: "", payload: null, from: "", to: "", preset: "month", tip: "",
        });
        onWillStart(() => this.load());
        onMounted(() => this.mountCharts());
        onPatched(() => {
            if (this.dirty) {
                this.dirty = false;
                this.destroyCharts();
                this.mountCharts();
            }
        });
        onWillUnmount(() => this.destroyCharts());
    }

    // ---------------------------------------------------------------- data
    async load(range) {
        this.state.loading = true;
        this.state.error = "";
        this.destroyCharts();
        try {
            const args = range ? [range.from, range.to] : [];
            const payload = await this.orm.call("ncollection.premium.dashboard", `get_${this.kind}`, args);
            this.state.payload = payload;
            this.state.from = payload.meta.period.from;
            this.state.to = payload.meta.period.to;
        } catch (error) {
            this.state.error = error?.data?.message || _t("The dashboard could not be loaded.");
        }
        this.state.loading = false;
        this.dirty = true;
    }

    presetRange(name) {
        const now = new Date();
        const y = now.getFullYear();
        const m = now.getMonth();
        if (name === "last_month") {
            return { from: iso(new Date(y, m - 1, 1)), to: iso(new Date(y, m, 0)) };
        }
        if (name === "quarter") {
            return { from: iso(new Date(y, Math.floor(m / 3) * 3, 1)), to: iso(now) };
        }
        if (name === "year") {
            return { from: iso(new Date(y, 0, 1)), to: iso(now) };
        }
        return { from: iso(new Date(y, m, 1)), to: iso(now) };
    }

    async selectPreset(name) {
        this.state.preset = name;
        await this.load(this.presetRange(name));
    }

    async applyCustom() {
        if (!this.state.from || !this.state.to) {
            return;
        }
        this.state.preset = "custom";
        await this.load({ from: this.state.from, to: this.state.to });
    }

    get presets() {
        return [
            { key: "month", label: _t("This month") },
            { key: "last_month", label: _t("Last month") },
            { key: "quarter", label: _t("This quarter") },
            { key: "year", label: _t("This year") },
        ];
    }

    // ---------------------------------------------------------------- words (translatable)
    get words() {
        return {
            apply: _t("Apply"), loading: _t("Loading…"), period: _t("Period"), from: _t("From"), to: _t("To"),
            excel: _t("Excel"), pdf: _t("PDF"), vsPrevious: _t("vs previous period"),
            emptyTitle: _t("No records in this period."),
            emptyBody: _t("Figures below are zero or not defined; none are estimated."),
        };
    }

    emptyBody() {
        return _t("Nothing has been recorded for %(company)s between these dates. %(rest)s", {
            company: this.state.payload.meta.company, rest: this.words.emptyBody,
        });
    }

    // ---------------------------------------------------------------- formatting (display only)
    number(value, digits = 0) {
        return (Number(value) + 0).toLocaleString("en-US", {
            minimumFractionDigits: digits,
            maximumFractionDigits: digits,
        });
    }

    money(value, compact = false) {
        const cur = this.state.payload?.meta?.currency;
        if (value === null || value === undefined || !cur) {
            return "—";
        }
        const text = compact && Math.abs(value) >= 10000
            ? new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value)
            : this.number(value, cur.decimals ?? 2);
        return cur.position === "before" ? `${cur.symbol} ${text}` : `${text} ${cur.symbol}`;
    }

    kpiValue(k) {
        if (k.value === null || k.value === undefined) {
            return "—";
        }
        if (k.kind === "currency") {
            return this.money(k.value);
        }
        if (k.kind === "percent") {
            return `${this.number(k.value, 1)}%`;
        }
        if (k.kind === "days") {
            return this.number(k.value, 1);
        }
        return this.number(k.value);
    }

    kpiUnit(k) {
        if (k.kind === "days") {
            return _t("days");
        }
        return "";
    }

    /** The comparison chip. Colour follows the business meaning (polarity); the arrow and the words carry it too. */
    delta(k) {
        if (k.delta_pct === null || k.delta_pct === undefined) {
            return null;
        }
        const up = k.delta_pct > 0;
        const flat = Math.abs(k.delta_pct) < 0.05;
        let tone = "neutral";
        if (!flat && k.polarity !== "neutral") {
            tone = (up && k.polarity === "up_good") || (!up && k.polarity === "up_bad") ? "good" : "bad";
        }
        const sign = flat ? "" : up ? "+" : "";
        return {
            tone,
            arrow: flat ? "▬" : up ? "▲" : "▼",
            text: `${sign}${this.number(k.delta_pct, 1)}%`,
        };
    }

    deltaWord(d) {
        return { good: _t("favourable"), bad: _t("unfavourable"), neutral: "" }[d.tone];
    }

    barWidth(table, row) {
        const peak = Math.max(...table.rows.map((r) => Math.abs(r.value || 0)), 0);
        return peak ? `${((Math.abs(row.value || 0) / peak) * 100).toFixed(1)}%` : "0%";
    }

    stateLabel(state) {
        return {
            overdue: _t("Overdue"), not_paid: _t("Unpaid"), partial: _t("Partial"), paid: _t("Paid"),
            in_payment: _t("In payment"), reversed: _t("Reversed"), draft: _t("Quotation"), sent: _t("Quotation"),
            sale: _t("Order"),
        }[state] || "";
    }

    toggleTip(key) {
        this.state.tip = this.state.tip === key ? "" : key;
    }

    // ---------------------------------------------------------------- navigation (ordinary actions: the ACLs apply)
    drill(k) {
        if (!k.drill) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window", name: k.drill.name, res_model: k.drill.model,
            views: [[false, "list"], [false, "form"]], domain: k.drill.domain,
        });
    }

    openRow(table, row) {
        if (!row.id) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window", res_model: table.model, res_id: row.id, views: [[false, "form"]],
        });
    }

    exportUrl(format) {
        const p = this.state.payload.meta.period;
        return `/ncollection/premium/${this.kind}/export/${format}?date_from=${p.from}&date_to=${p.to}`;
    }

    // ---------------------------------------------------------------- charts
    mountCharts() {
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
            // eslint-disable-next-line no-undef
            this.charts.push(new Chart(canvas, {
                type: chart.type,
                data: {
                    labels: chart.labels,
                    datasets: chart.series.map((s, i) => ({
                        label: s.name, data: s.data, backgroundColor: palette[i % palette.length],
                        borderColor: palette[i % palette.length], borderRadius: 4, maxBarThickness: 36,
                    })),
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    plugins: {
                        legend: { position: "bottom", labels: { usePointStyle: true, boxWidth: 8 } },
                        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${this.money(c.parsed.y)}` } },
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            title: { display: true, text: payload.meta.currency.name },
                            ticks: { callback: (v) => this.money(v, true) },
                        },
                        x: { grid: { display: false } },
                    },
                },
            }));
        }
    }

    destroyCharts() {
        this.charts.forEach((c) => c.destroy());
        this.charts = [];
    }
}

export class SalesPremiumDashboard extends NcPremiumDashboard {
    get kind() {
        return "sales";
    }
    get title() {
        return _t("Sales Analysis");
    }
}

export class InvoicingPremiumDashboard extends NcPremiumDashboard {
    get kind() {
        return "invoicing";
    }
    get title() {
        return _t("Invoicing Analysis");
    }
}

registry.category("actions").add("ncollection_account_dashboard.sales_premium", SalesPremiumDashboard);
registry.category("actions").add("ncollection_account_dashboard.invoicing_premium", InvoicingPremiumDashboard);
