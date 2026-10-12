/** @odoo-module **/
/*
 * The NCollection report viewer: ONE client action that renders every engine
 * report (Trial Balance, General Ledger, Balance Sheet, P&L, Partner Ledger,
 * Aged, ...). The report is chosen by `action.context.nc_report_model`.
 *
 * It decides NOTHING about accounting. The numbers are the engine's own rows
 * (`ncollection.report.service.nc_report_run` -> the same wizard that backs
 * the PDF and XLSX exports); this file only lays them out and links them.
 * Nothing is recomputed here except presentation sums of figures already on
 * screen (a ledger section's total), exactly as a list view's footer does.
 *
 * Every link is a real record: the row carries the id, and a click opens
 * that record's own form through the action service, so access control is
 * Odoo's. A value with no id is rendered as plain text, never as a link.
 *
 * State lives in the URL (?nc_df=...), so a reload - including the reload
 * Odoo does when the user switches language - comes back to the same report
 * with the same filters. A per-tab in-memory copy covers the breadcrumb "back"
 * from a drilled record (that route no longer carries our parameters). It is
 * never written to storage.
 */
import { Component, onMounted, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { router } from "@web/core/browser/router";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { formatFloat } from "@web/core/utils/numbers";
import { formatDate, deserializeDate } from "@web/core/l10n/dates";
import { user } from "@web/core/user";

// url key <- filter name
const URL_KEYS = {
    date_from: "nc_df", date_to: "nc_dt", target_move: "nc_tm", company_id: "nc_co",
    comparison_type: "nc_cmp", comparison_date_from: "nc_cf", comparison_date_to: "nc_ct",
    journal_ids: "nc_j", account_ids: "nc_a", partner_ids: "nc_p",
};
const M2M = ["journal_ids", "account_ids", "partner_ids"];
// per-tab memory for breadcrumb "back"; module scope on purpose, never stored
const LAST = {};
const PAGE = 400; // rows shown per section before "show more"

const PCT_KEYS = ["variance_pct", "ratio_pct"];
const DATE_KEYS = ["date"];

// The amount columns whose figure IS the sum of the journal items a drill opens (the report's own period / date range).
const DRILL_COLUMNS = ["current_amount", "debit", "credit", "balance"];

export class NcReportViewer extends Component {
    static template = "ncollection_account_reports.ReportViewer";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.model = this.props.action.context.nc_report_model;
        this.state = useState({
            loading: true,
            error: "",
            data: null,
            values: {},
            collapsed: {},   // group/row key -> true
            shown: {},       // group key -> rows shown
            picker: { field: "", term: "", options: [] },
            exportOpen: false,
            details: {},     // partner id -> { open, loading, rows }
        });
        this.mounted = false;
        this.pendingState = null;
        onWillStart(async () => {
            await this.run(this.initialFilters());
        });
        onMounted(() => {
            // The action manager only writes the URL for a MOUNTED controller.
            this.mounted = true;
            if (this.pendingState) {
                this.props.updateActionState(this.pendingState);
            }
        });
    }

    // ------------------------------------------------------------ state
    initialFilters() {
        const cur = router.current || {};
        const filters = {};
        let any = false;
        if (cur.nc_x) {
            try {
                Object.assign(filters, JSON.parse(cur.nc_x));
                any = true;
            } catch (error) {
            if (error && error.data && error.data.name) {
                throw error; // a real server error (AccessError, QWeb ...) is not a blocked download
            }
                // a hand-edited URL: ignore the extras, keep the rest
            }
        }
        for (const [name, key] of Object.entries(URL_KEYS)) {
            if (cur[key] === undefined) {
                continue;
            }
            any = true;
            filters[name] = M2M.includes(name)
                ? String(cur[key]).split(",").filter(Boolean).map(Number)
                : cur[key];
        }
        if (any) {
            return filters;
        }
        return LAST[this.model] ? { ...LAST[this.model] } : {};
    }

    isM2M(name) {
        const meta = this.state.data && this.state.data.filters.find((f) => f.name === name);
        return meta ? meta.type === "many2many" : M2M.includes(name);
    }

    filtersFromValues(values) {
        const out = {};
        for (const [name, v] of Object.entries(values)) {
            out[name] = this.isM2M(name) ? v.map((r) => r.id) : v;
        }
        return out;
    }

    async run(filters) {
        this.state.loading = true;
        this.state.error = "";
        const data = await this.orm.call("ncollection.report.service", "nc_report_run", [
            this.model,
            filters,
        ]);
        if (data.error) {
            this.state.error = data.error;
            this.state.loading = false;
            return;
        }
        this.state.data = data;
        this.state.values = JSON.parse(JSON.stringify(data.values));
        this.state.collapsed = {};
        this.state.shown = {};
        this.state.details = {};
        this.applyDefaultCollapse();
        this.persist(this.filtersFromValues(data.values));
        this.state.loading = false;
    }

    persist(filters) {
        LAST[this.model] = filters;
        const state = {};
        for (const [name, key] of Object.entries(URL_KEYS)) {
            const v = filters[name];
            state[key] = Array.isArray(v) ? (v.length ? v.join(",") : undefined) : v || undefined;
        }
        const extras = {};
        for (const [name, v] of Object.entries(filters)) {
            if (!(name in URL_KEYS)) {
                extras[name] = v;
            }
        }
        state.nc_x = Object.keys(extras).length ? JSON.stringify(extras) : undefined;
        // Through the action's own state channel, so Odoo writes OUR keys next to
        // its own (action id) instead of one overwriting the other.
        this.pendingState = state;
        if (this.mounted) {
            this.props.updateActionState(state);
        }
    }

    applyDefaultCollapse() {
        // A large ledger opens collapsed (sections one click away) instead of
        // rendering thousands of rows nobody scrolls.
        const rows = this.state.data.rows;
        if (this.isLedger && rows.length > 600) {
            for (const key of this.groupKeys) {
                this.state.collapsed[key] = true;
            }
        }
    }

    async apply() {
        await this.run(this.filtersFromValues(this.state.values));
    }

    async reset() {
        await this.run({});
    }

    // ------------------------------------------------------ report shape
    get columns() {
        return this.state.data ? this.state.data.columns : [];
    }

    get isLedger() {
        const d = this.state.data;
        return !!d && d.rows.some((r) => "is_initial" in r);
    }

    get groupBy() {
        // a ledger is "of" an account (General Ledger) or of a partner (Partner
        // Ledger / Statement, which list an Account column instead)
        return this.columns.some((c) => c.key === "account_name") ? "partner_id" : "account_id";
    }

    get groupKeys() {
        const keys = [];
        for (const r of this.state.data.rows) {
            if (r.is_initial) {
                keys.push(this.gkey(r));
            }
        }
        return keys;
    }

    gkey(row) {
        return "g" + row[this.groupBy];
    }

    /** Visible table body: [{kind, row, key, ...}] */
    get body() {
        const d = this.state.data;
        if (!d) {
            return [];
        }
        return this.isLedger ? this.ledgerBody() : this.treeBody();
    }

    ledgerBody() {
        const out = [];
        const rows = this.state.data.rows;
        let i = 0;
        while (i < rows.length) {
            const head = rows[i];
            const key = this.gkey(head);
            const items = [];
            i++;
            while (i < rows.length && !rows[i].is_initial) {
                items.push(rows[i]);
                i++;
            }
            const closed = !!this.state.collapsed[key];
            const closing = items.length ? items[items.length - 1].running_balance : head.running_balance;
            const debit = items.reduce((s, r) => s + (r.debit || 0), 0);
            const credit = items.reduce((s, r) => s + (r.credit || 0), 0);
            out.push({ kind: "section", key, row: head, count: items.length, closed, closing });
            if (!closed) {
                out.push({ kind: "opening", key: key + "o", row: head });
                const limit = this.state.shown[key] || PAGE;
                items.slice(0, limit).forEach((r, n) => out.push({ kind: "line", key: key + "l" + n, row: r }));
                if (items.length > limit) {
                    out.push({ kind: "more", key: key + "m", group: key, left: items.length - limit });
                }
                out.push({ kind: "total", key: key + "t", head, debit, credit, closing });
            }
        }
        return out;
    }

    treeBody() {
        // rows carry `level`; a row owns the following rows of a deeper level
        const rows = this.state.data.rows;
        const out = [];
        if (this.state.data.detail) {
            return this.detailBody(rows);
        }
        let hideBelow = null;
        rows.forEach((row, idx) => {
            const level = row.level || 0;
            if (hideBelow !== null) {
                if (level > hideBelow) {
                    return;
                }
                hideBelow = null;
            }
            const next = rows[idx + 1];
            const hasChildren = !!next && (next.level || 0) > level;
            const key = "r" + idx;
            out.push({ kind: "row", key, row, level, hasChildren, closed: !!this.state.collapsed[key] });
            if (hasChildren && this.state.collapsed[key]) {
                hideBelow = level;
            }
        });
        return out;
    }

    /** Summary rows that expand into their documents (Aged: open invoices). */
    detailBody(rows) {
        const out = [];
        for (const row of rows) {
            const d = this.state.details[row.partner_id];
            out.push({ kind: "row", key: "p" + row.partner_id, row, level: 0, hasChildren: false,
                       hasDetail: true, closed: !(d && d.open) });
            if (d && d.open) {
                if (d.loading) {
                    out.push({ kind: "loading", key: "pl" + row.partner_id });
                }
                for (const line of d.rows || []) {
                    out.push({ kind: "detail", key: "pd" + row.partner_id + "_" + line.move_id + line.due, line });
                }
            }
        }
        return out;
    }

    async toggleDetail(row) {
        const id = row.partner_id;
        if (!this.state.details[id]) {
            this.state.details[id] = { open: false, loading: false, rows: null };
        }
        // re-read through the reactive state: mutating the object we just
        // assigned would not re-render
        const d = this.state.details[id];
        d.open = !d.open;
        if (d.open && d.rows === null) {
            d.loading = true;
            d.rows = await this.orm.call("ncollection.report.service", "nc_report_detail", [
                this.state.data.wizard.model, this.state.data.wizard.id, id,
            ]);
            d.loading = false;
        }
    }

    toggle(key) {
        this.state.collapsed[key] = !this.state.collapsed[key];
    }

    expandAll() {
        this.state.collapsed = {};
    }

    collapseAll() {
        const rows = this.state.data.rows;
        const next = {};
        if (this.isLedger) {
            for (const key of this.groupKeys) {
                next[key] = true;
            }
        } else {
            rows.forEach((row, idx) => {
                const n = rows[idx + 1];
                if (n && (n.level || 0) > (row.level || 0) && (row.level || 0) <= 1) {
                    next["r" + idx] = true;
                }
            });
        }
        this.state.collapsed = next;
    }

    showMore(group) {
        this.state.shown[group] = (this.state.shown[group] || PAGE) + PAGE;
    }

    // ------------------------------------------------------- formatting
    isMoney(col) {
        return col.type === "monetary" || PCT_KEYS.includes(col.key);
    }

    amount(value, col) {
        if (PCT_KEYS.includes(col.key)) {
            return formatFloat(value || 0, { digits: [16, 2] }) + "%";
        }
        return formatFloat(value || 0, { digits: [16, this.state.data.currency.digits] });
    }

    money(value) {
        return formatFloat(value || 0, { digits: [16, this.state.data.currency.digits] });
    }

    isNegative(value) {
        return typeof value === "number" && value < 0;
    }

    date(value) {
        // Latin digits in every language, matching the amounts beside them.
        return value
            ? formatDate(deserializeDate(value)).replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x0660))
            : "";
    }

    showMoreLabel(left) {
        return _t("Show more (%s remaining)", left);
    }

    cell(row, col) {
        const v = row[col.key];
        if (DATE_KEYS.includes(col.key)) {
            return this.date(v);
        }
        return v === undefined || v === null || v === false ? "" : v;
    }

    /** The record a cell links to, or null: never a link without an id. */
    link(row, col) {
        const k = col.key;
        if (row.is_initial) {
            return null;
        }
        if (k === "move_name" && row.move_id) {
            return { model: "account.move", id: row.move_id };
        }
        if (k === "journal_name" && row.journal_id) {
            return { model: "account.journal", id: row.journal_id };
        }
        if (k === "partner_name" && row.partner_id) {
            return { model: "res.partner", id: row.partner_id };
        }
        if (k === "account_name" && row.account_id) {
            return { model: "account.account", id: row.account_id };
        }
        if (k === "label" && row.tax_id) {
            return { model: "account.tax", id: row.tax_id };
        }
        if (k === "label" && row.link_partner && row.partner_id) {
            return { model: "res.partner", id: row.partner_id };
        }
        if ((k === "label") && row.account_id && !this.isLedger) {
            return { model: "account.account", id: row.account_id };
        }
        return null;
    }

    /** A summary row that stands for ONE account (never a group or a total) can open its journal items. */
    canDrill(row, col) {
        // Only the figures the drill domain reproduces: the current / period amounts. Opening, closing, previous
        // period and variance columns are other sums, and opening the current period's items under them would lie.
        return !!(row.account_id && !row.is_initial && !this.isLedger && DRILL_COLUMNS.includes(col.key)
            && this.state.data && this.state.data.wizard);
    }

    /** Open the journal items behind a row: the server builds the domain (the report's own filters + the row's
     *  account) and checks the run; this only opens what it returns. */
    async drillRow(row) {
        try {
            const action = await this.orm.call(
                "ncollection.report.service", "nc_report_drill",
                [this.state.data.wizard.model, this.state.data.wizard.id],
                { account_id: row.account_id, label: row.label || "" }
            );
            await this.action.doAction(action);
        } catch {
            this.notification.add(_t("These journal items cannot be opened."), { type: "warning" });
        }
    }

    openRecord(link) {
        if (!link) {
            return;
        }
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: link.model,
            res_id: link.id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    accountLabel(id) {
        const a = this.state.data.accounts[id];
        return a ? (a.code ? `${a.code} — ${a.name}` : a.name) : "";
    }

    partnerLabel(id) {
        return this.state.data.partners[id] || "";
    }

    sectionLabel(row) {
        return this.groupBy === "partner_id" ? this.partnerLabel(row.partner_id) : this.accountLabel(row.account_id);
    }

    sectionLink(row) {
        return this.groupBy === "partner_id" && row.partner_id
            ? { model: "res.partner", id: row.partner_id }
            : row.account_id ? { model: "account.account", id: row.account_id } : null;
    }

    /** Drill from a ledger line's source documents (only those that exist). */
    sources(row) {
        const out = [];
        if (row.payment_id) {
            out.push({ label: _t("Payment"), model: "account.payment", id: row.payment_id });
        }
        if (row.sale_order_id) {
            out.push({ label: _t("Sale order"), model: "sale.order", id: row.sale_order_id });
        }
        if (row.purchase_order_id) {
            out.push({ label: _t("Purchase order"), model: "purchase.order", id: row.purchase_order_id });
        }
        return out;
    }

    openMoveLine(row) {
        if (row.move_line_id) {
            return this.openRecord({ model: "account.move.line", id: row.move_line_id });
        }
    }

    // ----------------------------------------------------------- header
    get periodText() {
        const p = this.state.data.period;
        if (!p.from && !p.to) {
            return "";
        }
        return `${p.from ? this.date(p.from) : "…"} → ${p.to ? this.date(p.to) : "…"}`;
    }

    get chips() {
        const out = [];
        for (const f of this.state.data.filters) {
            if (!M2M.includes(f.name)) {
                continue;
            }
            const sel = this.state.data.values[f.name] || [];
            if (sel.length) {
                out.push({ label: f.label, text: sel.map((r) => r.name).join(", ") });
            }
        }
        return out;
    }

    has(name) {
        return !!this.state.data && this.state.data.filters.some((f) => f.name === name);
    }

    field(name) {
        return this.state.data.filters.find((f) => f.name === name);
    }

    /** Filters beyond the shared set (Aged: receivable/payable, bucket size...). */
    get extraFilters() {
        return this.state.data.filters.filter((f) => !f.shared);
    }

    get showExpand() {
        return this.isLedger || this.state.data.rows.some((r, i, a) => a[i + 1] && (a[i + 1].level || 0) > (r.level || 0));
    }

    get multiCompany() {
        return (user.allowedCompanies || []).length > 1;
    }

    get companies() {
        return user.allowedCompanies || [];
    }

    // --------------------------------------------------------- pickers
    async searchRelation(field, term) {
        this.state.picker.field = field.name;
        this.state.picker.term = term;
        // answers can arrive out of order; only the latest keystroke may paint
        const ticket = (this.searchTicket = (this.searchTicket || 0) + 1);
        if (!term || term.length < 1) {
            this.state.picker.options = [];
            return;
        }
        const recs = await this.orm.call(field.relation, "name_search", [term], { limit: 8 });
        if (ticket !== this.searchTicket) {
            return;
        }
        const taken = new Set((this.state.values[field.name] || []).map((r) => r.id));
        this.state.picker.options = recs
            .filter(([id]) => !taken.has(id))
            .map(([id, name]) => ({ id, name }));
    }

    pick(field, option) {
        this.state.values[field.name].push(option);
        this.state.picker = { field: "", term: "", options: [] };
    }

    unpick(field, id) {
        this.state.values[field.name] = this.state.values[field.name].filter((r) => r.id !== id);
    }

    // ----------------------------------------------------------- export
    async exportReport(kind) {
        this.state.exportOpen = false;
        const act = await this.orm.call("ncollection.report.service", "nc_report_export", [
            this.state.data.wizard.model, this.state.data.wizard.id, kind,
        ]);
        if (kind !== "pdf") {
            return this.action.doAction(act);
        }
        // Odoo downloads a PDF with a background request. A download manager or a
        // browser policy can swallow that response (seen: IDM answering 204), and
        // Odoo then reports it as "Arbitrary Uncaught Python Exception" although
        // the server produced a valid PDF. Say what happened and open the same
        // report in a tab instead of leaving the user with a stack-trace dialog.
        try {
            return await this.action.doAction(act);
        } catch {
            const ids = (act.context && act.context.active_ids) || [this.state.data.wizard.id];
            this.notification.add(
                _t("The PDF download was blocked by the browser or a download manager. Opening the report in a new tab instead."),
                { type: "warning", sticky: false }
            );
            browser.open(`/report/pdf/${act.report_name}/${ids.join(",")}`, "_blank");
        }
    }
}

registry.category("actions").add("ncollection_account_reports.report_viewer", NcReportViewer);
