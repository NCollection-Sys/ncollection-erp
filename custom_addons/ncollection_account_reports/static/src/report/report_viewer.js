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
import {
    Component, onMounted, onWillStart, onWillUnmount, useExternalListener, useRef, useState,
} from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { router } from "@web/core/browser/router";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { formatFloat } from "@web/core/utils/numbers";
import { formatDate, deserializeDate } from "@web/core/l10n/dates";
import { user } from "@web/core/user";

const { DateTime } = luxon;

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
            picker: { field: "", term: "", options: [], active: -1, more: false, loading: false, open: false },
            exportOpen: false,
            xbrlHelp: false,
            details: {},     // partner id -> { open, loading, rows }
            moreOpen: false, // the advanced-filters row
            periodCustom: false, // Custom chosen in the Period list
        });
        this.mounted = false;
        this.pendingState = null;
        this.exportMenu = useRef("exportMenu");
        this.exportButton = useRef("exportButton");
        useExternalListener(window, "pointerdown", (ev) => this.onWindowPointerDown(ev), { capture: true });
        useExternalListener(window, "keydown", (ev) => this.onWindowKeydown(ev));
        onWillUnmount(() => browser.clearTimeout(this.searchTimer));
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
        // A drill from another report hands its run's filters over (e.g. Account Balances -> the account's ledger).
        const preset = this.props.action.context && this.props.action.context.nc_report_filters;
        if (preset && typeof preset === "object") {
            return { ...preset };
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

    /** Request 09.2: a ledger's Debit / Credit cells in the company's Financial Colours, from the report's own
     *  perspective: on a receivable (or any non-payable) ledger a debit adds, on a supplier ledger a credit adds. */
    finClass(col, row) {
        if (!row || !row[col.key] || (col.key !== "debit" && col.key !== "credit")) {
            return "";
        }
        const payable = this.state.values && this.state.values.partner_scope === "payable";
        const adds = payable ? col.key === "credit" : col.key === "debit";
        return adds ? "o_nc_fin_in" : "o_nc_fin_out";
    }

    async apply() {
        await this.run(this.filtersFromValues(this.state.values));
    }

    /** Request 04: switching Scope clears the partners picked for the other scope, so no hidden stale filter ever
     *  narrows the new run. The server enforces the same rule (nc_report_run drops ineligible partners). */
    onScopeChange() {
        if (this.state.values.partner_ids && this.state.values.partner_ids.length) {
            this.state.values.partner_ids = [];
            this.notification.add(_t("Partners cleared: pick them again for this scope."), { type: "info" });
        }
        this.closePicker();
    }

    async reset() {
        await this.run({});
    }

    // ------------------------------------------------------ compact filter bar
    /** Period presets: they only fill From / To (the user still presses Apply); "custom" = whatever is typed. */
    get periodPresets() {
        return [
            ["custom", _t("Custom")], ["this_month", _t("This month")], ["last_month", _t("Last month")],
            ["this_quarter", _t("This quarter")], ["last_quarter", _t("Last quarter")],
            ["this_year", _t("This calendar year")], ["last_year", _t("Last calendar year")],
        ];
    }

    presetRange(key) {
        const now = DateTime.local();
        const fmt = (d) => d.toISODate();
        switch (key) {
            case "this_month": return [now.startOf("month"), now.endOf("month")].map(fmt);
            case "last_month": { const d = now.minus({ months: 1 }); return [d.startOf("month"), d.endOf("month")].map(fmt); }
            case "this_quarter": return [now.startOf("quarter"), now.endOf("quarter")].map(fmt);
            case "last_quarter": { const d = now.minus({ quarters: 1 }); return [d.startOf("quarter"), d.endOf("quarter")].map(fmt); }
            case "this_year": return [now.startOf("year"), now.endOf("year")].map(fmt);
            case "last_year": { const d = now.minus({ years: 1 }); return [d.startOf("year"), d.endOf("year")].map(fmt); }
        }
        return null;
    }

    get periodPreset() {
        if (this.state.periodCustom) {
            return "custom";              // the user chose Custom: never snap back to a matching preset
        }
        const v = this.state.values;
        for (const [key] of this.periodPresets) {
            const r = this.presetRange(key);
            if (r && r[0] === v.date_from && r[1] === v.date_to) {
                return key;
            }
        }
        return "custom";
    }

    applyPeriodPreset(key) {
        const r = this.presetRange(key);
        this.state.periodCustom = !r;
        if (r) {
            this.state.values.date_from = r[0];
            this.state.values.date_to = r[1];
        }
    }

    /** The Customers / Suppliers / All switch of the statement (the report's own partner_scope filter). */
    get scopeFilter() {
        return this.state.data ? this.state.data.filters.find((f) => f.name === "partner_scope" && f.type === "selection") : null;
    }

    setScope(value) {
        if (this.state.values.partner_scope !== value) {
            this.state.values.partner_scope = value;
            this.onScopeChange();
        }
    }

    get moreExtraFilters() {
        return this.extraFilters.filter((f) => f.name !== "partner_scope");
    }

    get hasMoreFilters() {
        return ["target_move", "comparison_type", "journal_ids", "account_ids"].some((n) => this.has(n))
            || (this.has("company_id") && this.multiCompany) || this.moreExtraFilters.length > 0;
    }

    /** How many advanced filters differ from the report's own default (sent by the server per filter), so a folded
     *  row never hides an active filter: Entries incl. drafts, another company, comparison, journals / accounts,
     *  every extra filter of any type. */
    get moreFiltersCount() {
        const v = this.state.values;
        const differs = (f) => {
            const val = v[f.name];
            if (f.type === "many2many") {
                return (val || []).length > 0;
            }
            if ("default" in f) {
                return (val ?? false) !== (f.default ?? false) && !(val === "" && !f.default);
            }
            return f.type === "boolean" ? !!val : false;
        };
        let n = 0;
        for (const name of ["target_move", "comparison_type", "journal_ids", "account_ids"]) {
            if (this.has(name) && differs(this.field(name))) {
                n++;
            }
        }
        if (this.has("company_id") && this.multiCompany && differs(this.field("company_id"))) {
            n++;
        }
        for (const f of this.moreExtraFilters) {
            if (differs(f)) {
                n++;
            }
        }
        return n;
    }

    /** Scope as toggle buttons: Left / Right (mirrored in RTL) move the choice, like a segmented control. */
    onScopeKeydown(ev) {
        const opts = this.scopeFilter ? this.scopeFilter.options.map((o) => o[0]) : [];
        const i = opts.indexOf(this.state.values.partner_scope);
        const rtl = getComputedStyle(ev.currentTarget).direction === "rtl";
        const step = { ArrowRight: rtl ? -1 : 1, ArrowLeft: rtl ? 1 : -1 }[ev.key];
        if (!step || i < 0) {
            return;
        }
        ev.preventDefault();
        const next = opts[(i + step + opts.length) % opts.length];
        this.setScope(next);
        const btn = ev.currentTarget.querySelector(`[data-scope="${next}"]`);
        if (btn) {
            btn.focus();
        }
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
    // Journals / Accounts / Partners (and a report's extra many2many filters): the suggestions open as soon as the
    // field is focused (empty term = the first records the user may choose, for the report's company), typing
    // filters them (debounced), arrows + Enter pick, Escape / a click outside close. The server decides what may
    // be listed (ncollection.report.service.nc_report_suggest runs as the user, no sudo).
    openPicker(field) {
        if (this.state.picker.field !== field.name) {
            this.state.picker = { field: field.name, term: "", options: [], active: -1, more: false,
                                  loading: false, open: true };
        }
        this.state.picker.open = true;
        this.closeExport();
        this.fetchSuggestions(field, this.state.picker.term, false);
    }

    searchRelation(field, term) {
        if (this.state.picker.field !== field.name) {
            this.openPicker(field);
        }
        this.state.picker.term = term;
        this.state.picker.open = true;
        browser.clearTimeout(this.searchTimer);
        this.searchTimer = browser.setTimeout(() => this.fetchSuggestions(field, term, false), 200);
    }

    async fetchSuggestions(field, term, append) {
        // answers can arrive out of order; only the latest request may paint
        const ticket = (this.searchTicket = (this.searchTicket || 0) + 1);
        const picker = this.state.picker;
        picker.loading = true;
        const taken = (this.state.values[field.name] || []).map((r) => r.id);
        const result = await this.orm.call("ncollection.report.service", "nc_report_suggest", [
            this.state.data.wizard.model, field.name, term || "",
        ], {
            company_id: this.state.values.company_id || false,
            exclude_ids: taken,
            // a statement / partner ledger offers only partners of the chosen Scope (customers / suppliers)
            scope: this.state.values.partner_scope || false,
            offset: append ? picker.options.length : 0,
            limit: 20,
        });
        if (ticket !== this.searchTicket || this.state.picker.field !== field.name) {
            return;
        }
        picker.options = append ? picker.options.concat(result.records) : result.records;
        picker.more = result.more;
        picker.loading = false;
        if (!append) {
            picker.active = picker.options.length ? 0 : -1;
        }
    }

    loadMore(field) {
        this.fetchSuggestions(field, this.state.picker.term, true);
    }

    onPickerKeydown(field, ev) {
        const picker = this.state.picker;
        if (ev.key === "Escape") {
            if (picker.open) {
                ev.preventDefault();
                ev.stopPropagation();
                this.closePicker();
            }
            return;
        }
        if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
            ev.preventDefault();
            if (!picker.open || picker.field !== field.name) {
                this.openPicker(field);
                return;
            }
            const n = picker.options.length;
            if (n) {
                picker.active = (picker.active + (ev.key === "ArrowDown" ? 1 : n - 1)) % n;
                const el = ev.target.closest(".o_nc_rep__f--picker")?.querySelector(
                    `.o_nc_rep__options [data-index="${picker.active}"]`);
                el?.scrollIntoView({ block: "nearest" });
            }
            return;
        }
        if (ev.key === "Enter") {
            ev.preventDefault();      // never submit / refresh the report from a picker
            const option = picker.options[picker.active];
            if (picker.open && option) {
                this.pick(field, option, ev.target);
            }
            return;
        }
        if (ev.key === "Backspace" && !ev.target.value && (this.state.values[field.name] || []).length) {
            const values = this.state.values[field.name];
            this.unpick(field, values[values.length - 1].id);
        }
        if (ev.key === "Tab") {
            this.closePicker();
        }
    }

    closePicker() {
        browser.clearTimeout(this.searchTimer);
        this.searchTicket = (this.searchTicket || 0) + 1;
        this.state.picker = { field: "", term: "", options: [], active: -1, more: false, loading: false,
                              open: false };
    }

    pick(field, option, input) {
        this.state.values[field.name].push({ id: option.id, name: option.name });
        // stay in the field, ready for the next choice (multi-selection is kept)
        this.state.picker.term = "";
        this.state.picker.options = this.state.picker.options.filter((o) => o.id !== option.id);
        this.state.picker.active = Math.min(this.state.picker.active, this.state.picker.options.length - 1);
        if (input) {
            input.value = "";
            input.focus();
        }
        this.fetchSuggestions(field, "", false);
    }

    unpick(field, id) {
        this.state.values[field.name] = this.state.values[field.name].filter((r) => r.id !== id);
        if (this.state.picker.field === field.name && this.state.picker.open) {
            this.fetchSuggestions(field, this.state.picker.term, false);
        }
    }

    // ------------------------------------------------------ dropdowns
    toggleExport() {
        if (this.state.exportOpen) {
            this.closeExport();
            return;
        }
        this.closePicker();
        this.state.exportOpen = true;
        this.state.xbrlHelp = false;
    }

    closeExport(restoreFocus = false) {
        if (!this.state.exportOpen) {
            return;
        }
        this.state.exportOpen = false;
        this.state.xbrlHelp = false;
        if (restoreFocus) {
            this.exportButton.el?.focus();
        }
    }

    onExportKeydown(ev) {
        if (ev.key === "Escape") {
            ev.preventDefault();
            ev.stopPropagation();
            this.closeExport(true);
            return;
        }
        if (ev.key !== "ArrowDown" && ev.key !== "ArrowUp" && ev.key !== "Home" && ev.key !== "End") {
            return;
        }
        ev.preventDefault();
        if (!this.state.exportOpen) {
            this.state.exportOpen = true;
            return;
        }
        const items = [...(this.exportMenu.el?.querySelectorAll('[role="menuitem"]:not([disabled])') || [])];
        if (!items.length) {
            return;
        }
        const i = items.indexOf(document.activeElement);
        let next = 0;
        if (ev.key === "End") {
            next = items.length - 1;
        } else if (ev.key === "ArrowDown") {
            next = i < 0 ? 0 : (i + 1) % items.length;
        } else if (ev.key === "ArrowUp") {
            next = i <= 0 ? items.length - 1 : i - 1;
        }
        items[next].focus();
    }

    // One window listener closes whatever is open when the user presses or taps OUTSIDE it (capture phase: the
    // click is not swallowed and nothing else is triggered, no refresh, no download), and on Escape.
    onWindowPointerDown(ev) {
        const target = ev.target;
        if (this.state.exportOpen && !this.exportMenu.el?.contains(target)) {
            this.closeExport();
        }
        if (this.state.picker.open) {
            const box = target.closest && target.closest(".o_nc_rep__f--picker");
            if (!box || box.dataset.field !== this.state.picker.field) {
                this.closePicker();
            }
        }
    }

    onExportFocusout(ev) {
        // Tab away from the menu closes it (focus moved to something outside it)
        if (this.state.exportOpen && ev.relatedTarget && !this.exportMenu.el?.contains(ev.relatedTarget)) {
            this.closeExport();
        }
    }

    onWindowKeydown(ev) {
        if (ev.key !== "Escape") {
            return;
        }
        if (this.state.exportOpen) {
            this.closeExport(true);
        }
        if (this.state.picker.open) {
            this.closePicker();
        }
    }

    // ----------------------------------------------------------- print
    // Print opens the PDF in a new tab (the browser's viewer prints it); it never downloads. The PDF is the very
    // report action Export's PDF uses (nc_report_export: same report, same wizard record holding the filters, same
    // print payload and template), so what the tab shows is what Export would save.
    async printReport() {
        this.state.exportOpen = false;
        const act = await this.orm.call("ncollection.report.service", "nc_report_export", [
            this.state.data.wizard.model, this.state.data.wizard.id, "pdf",
        ]);
        const ids = (act.context && act.context.active_ids) || [this.state.data.wizard.id];
        browser.open(`/report/pdf/${act.report_name}/${ids.join(",")}`, "_blank");
    }

    // ----------------------------------------------------------- export
    async exportReport(kind) {
        this.state.exportOpen = false;
        const act = await this.orm.call("ncollection.report.service", "nc_report_export", [
            this.state.data.wizard.model, this.state.data.wizard.id, kind,
        ]);
        if (kind !== "pdf") {
            // xlsx and xbrl are downloads (act_url with target download)
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
