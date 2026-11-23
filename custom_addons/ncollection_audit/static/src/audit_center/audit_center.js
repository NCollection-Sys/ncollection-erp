/** @odoo-module **/
/*
 * Audit Log 2.0 - the History screen (one client action).
 *
 * It decides nothing. Every row, value, version and Undo verdict comes from the server
 * (`ncollection.audit.service` / `ncollection.audit.undo`), which runs as the user and applies the company rule, the
 * model rule and the field mask. This file lays results out, and an Undo is only ever SENT with the fingerprint of the
 * preview the user is looking at, so the server refuses it if the document changed in between.
 *
 * Nothing is stored: reopening the screen shows the defaults again (newest first, no filters).
 */
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";
import { localization } from "@web/core/l10n/localization";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

const PAGE_SIZE = 50;
const EMPTY_FILTERS = () => ({
    date_from: "", date_to: "", user_ids: [], actions: [], doc_types: [], company_ids: [],
    reference: "", res_id: "", search: "", reversed: "",
});

export class NcAuditCenter extends Component {
    static template = "ncollection_audit.AuditCenter";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        const record = this.props.action?.context?.nc_audit_record || null;
        this.state = useState({
            loading: true, options: null, rows: [], total: 0, offset: 0,
            sort: "date", direction: "desc",
            filters: EMPTY_FILTERS(), draft: EMPTY_FILTERS(), more: false,
            userQuery: "", typeQuery: "", userOpen: false, typeOpen: false,
            record, view: record ? "history" : "list",
            event: null, tab: "changes", showAllLines: false,
            history: null, compare: null, compareA: null, compareB: null, showUnchanged: false,
            undo: null, undoSelected: {}, undoBusy: false, undoDone: null, error: "",
        });
        onWillStart(async () => {
            this.state.options = await this.orm.call("ncollection.audit.service", "nc_audit_options", []);
            if (record) {
                await this.openHistory(record.model, record.res_id);
            } else {
                await this.load();
            }
        });
    }

    // ---- formatting -------------------------------------------------------

    fmtDate(value) {
        if (!value) {
            return "";
        }
        // Always with seconds (two events in the same minute must be told apart), in the user's own time zone.
        let format = localization.dateTimeFormat;
        if (!/s/.test(format)) {
            format = format.replace(/mm/, "mm:ss");
        }
        return formatDateTime(deserializeDateTime(value), { format });
    }
    actionLabel(key) {
        return { create: _t("Create"), update: _t("Update"), delete: _t("Delete") }[key] || _t("Other");
    }
    get pageLabel() {
        if (!this.state.total) {
            return "";
        }
        const start = this.state.offset + 1;
        const end = Math.min(this.state.offset + PAGE_SIZE, this.state.total);
        return _t("%(start)s-%(end)s of %(total)s", { start, end, total: this.state.total });
    }
    get activeChips() {
        const f = this.state.filters;
        const opts = this.state.options || {};
        const chips = [];
        if (f.date_from || f.date_to) {
            chips.push({ key: "date", label: `${f.date_from || "…"} → ${f.date_to || "…"}` });
        }
        for (const id of f.user_ids) {
            const user = (opts.users || []).find((u) => u.id === id);
            chips.push({ key: "user", value: id, label: user ? user.name : `#${id}` });
        }
        for (const a of f.actions) {
            chips.push({ key: "action", value: a, label: this.actionLabel(a) });
        }
        for (const t of f.doc_types) {
            const type = (opts.doc_types || []).find((d) => d.key === t);
            chips.push({ key: "type", value: t, label: type ? type.label : t });
        }
        for (const id of f.company_ids) {
            const company = (opts.companies || []).find((c) => c.id === id);
            chips.push({ key: "company", value: id, label: company ? company.name : `#${id}` });
        }
        if (f.reference) {
            chips.push({ key: "reference", label: _t("Reference: %s", f.reference) });
        }
        if (f.res_id) {
            chips.push({ key: "res_id", label: _t("Record #%s", f.res_id) });
        }
        if (f.search) {
            chips.push({ key: "search", label: _t("Contains: %s", f.search) });
        }
        if (f.reversed) {
            const labels = { reversed: _t("Undone"), not_reversed: _t("Not undone"), undo_events: _t("Undo events") };
            chips.push({ key: "reversed", label: labels[f.reversed] });
        }
        return chips;
    }
    get moreCount() {
        const d = this.state.draft;
        return [d.company_ids.length, d.reference, d.res_id, d.search, d.reversed].filter(Boolean).length;
    }
    get filteredUsers() {
        const q = this.state.userQuery.toLowerCase();
        return (this.state.options?.users || []).filter((u) => !q || (u.name || "").toLowerCase().includes(q));
    }
    get filteredTypes() {
        const q = this.state.typeQuery.toLowerCase();
        return (this.state.options?.doc_types || []).filter((t) => !q || t.label.toLowerCase().includes(q));
    }
    get showCompany() {
        return !!this.state.options?.multi_company;
    }

    // ---- list -------------------------------------------------------------

    serverFilters() {
        const f = this.state.filters;
        const out = { ...f };
        out.res_id = f.res_id ? parseInt(f.res_id, 10) || undefined : undefined;
        for (const key of Object.keys(out)) {
            if (out[key] === "" || out[key] === undefined) {
                delete out[key];
            }
        }
        return out;
    }

    async load() {
        this.state.loading = true;
        this.state.error = "";
        try {
            const result = await this.orm.call("ncollection.audit.service", "nc_audit_search", [], {
                filters: this.serverFilters(), offset: this.state.offset, limit: PAGE_SIZE,
                sort: this.state.sort, direction: this.state.direction,
            });
            this.state.rows = result.rows;
            this.state.total = result.total;
        } catch (error) {
            this.state.rows = [];
            this.state.total = 0;
            this.state.error = error.data?.message || error.message || String(error);
        } finally {
            this.state.loading = false;
        }
    }

    toggleDraft(key, value) {
        const list = this.state.draft[key];
        const index = list.indexOf(value);
        if (index >= 0) {
            list.splice(index, 1);
        } else {
            list.push(value);
        }
    }
    setAction(value) {
        this.state.draft.actions = value ? [value] : [];
    }
    isAction(value) {
        const actions = this.state.draft.actions;
        return value ? actions.length === 1 && actions[0] === value : actions.length === 0;
    }
    apply() {
        this.state.filters = JSON.parse(JSON.stringify(this.state.draft));
        this.state.offset = 0;
        this.state.userOpen = this.state.typeOpen = false;
        return this.load();
    }
    reset() {
        this.state.filters = EMPTY_FILTERS();
        this.state.draft = EMPTY_FILTERS();
        this.state.sort = "date";
        this.state.direction = "desc";
        this.state.offset = 0;
        return this.load();
    }
    removeChip(chip) {
        const d = this.state.draft;
        if (chip.key === "date") {
            d.date_from = d.date_to = "";
        } else if (chip.key === "user") {
            d.user_ids = d.user_ids.filter((v) => v !== chip.value);
        } else if (chip.key === "action") {
            d.actions = d.actions.filter((v) => v !== chip.value);
        } else if (chip.key === "type") {
            d.doc_types = d.doc_types.filter((v) => v !== chip.value);
        } else if (chip.key === "company") {
            d.company_ids = d.company_ids.filter((v) => v !== chip.value);
        } else {
            d[chip.key] = "";
        }
        return this.apply();
    }
    onKeyApply(ev) {
        if (ev.key === "Enter") {
            this.apply();
        }
    }
    sortBy(column) {
        if (this.state.sort === column) {
            this.state.direction = this.state.direction === "desc" ? "asc" : "desc";
        } else {
            this.state.sort = column;
            this.state.direction = column === "date" ? "desc" : "asc";
        }
        this.state.offset = 0;
        return this.load();
    }
    ariaSort(column) {
        if (this.state.sort !== column) {
            return "none";
        }
        return this.state.direction === "asc" ? "ascending" : "descending";
    }
    page(delta) {
        const next = this.state.offset + delta * PAGE_SIZE;
        if (next < 0 || next >= this.state.total) {
            return;
        }
        this.state.offset = next;
        return this.load();
    }

    // ---- one event --------------------------------------------------------

    async openEvent(id, tab = "changes") {
        this.state.error = "";
        try {
            this.state.event = await this.orm.call("ncollection.audit.service", "nc_audit_event", [id]);
        } catch (error) {
            this.notification.add(error.data?.message || String(error), { type: "danger" });
            return;
        }
        this.state.view = "event";
        this.state.showAllLines = false;
        this.state.history = null;
        this.state.compare = null;
        this.state.undo = null;
        this.state.undoDone = null;
        await this.setTab(tab);
    }
    onRowKey(ev, id) {
        if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            this.openEvent(id);
        }
    }
    async setTab(tab) {
        this.state.tab = tab;
        const event = this.state.event;
        if ((tab === "history" || tab === "compare") && !this.state.history && event) {
            this.state.history = await this.orm.call("ncollection.audit.service", "nc_audit_history",
                [event.model, event.res_id]);
            this.defaultCompare();
        }
        if (tab === "compare" && !this.state.compare) {
            await this.runCompare();
        }
        if (tab === "undo" && !this.state.undo) {
            await this.previewUndo();
        }
    }
    backToList() {
        this.state.view = this.state.record && !this.state.event ? "history" : "list";
        this.state.event = null;
        if (this.state.view === "list" && !this.state.rows.length) {
            return this.load();
        }
    }
    async openDocument() {
        const action = await this.orm.call("ncollection.audit.service", "nc_audit_open_document",
            [this.state.event.id]);
        this.action.doAction(action);
    }
    get visibleLines() {
        const lines = this.state.event?.lines || [];
        return this.state.showAllLines ? lines : lines.slice(0, 12);
    }

    // ---- record timeline & versions --------------------------------------

    async openHistory(model, resId) {
        this.state.history = await this.orm.call("ncollection.audit.service", "nc_audit_history", [model, resId]);
        this.state.view = "history";
        this.state.compare = null;
        this.defaultCompare();
    }
    defaultCompare() {
        const versions = this.state.history?.versions || [];
        if (versions.length >= 2) {
            const current = this.state.event && versions.find((v) => v.id === this.state.event.id);
            const b = current || versions[versions.length - 1];
            const index = versions.indexOf(b);
            this.state.compareB = b.id;
            this.state.compareA = versions[index > 0 ? index - 1 : 1].id;
        }
    }
    async runCompare() {
        const h = this.state.history;
        if (!h || h.versions.length < 2 || !this.state.compareA || !this.state.compareB) {
            this.state.compare = null;
            return;
        }
        this.state.compare = await this.orm.call("ncollection.audit.service", "nc_audit_compare",
            [h.model, h.res_id, parseInt(this.state.compareA, 10), parseInt(this.state.compareB, 10)]);
    }
    async compareFromTimeline(a, b) {
        this.state.compareA = a;
        this.state.compareB = b;
        await this.runCompare();
        if (this.state.view === "event") {
            this.state.tab = "compare";
        }
    }
    get compareRows() {
        const rows = this.state.compare?.rows || [];
        return this.state.showUnchanged ? rows : rows.filter((r) => r.status !== "unchanged");
    }
    statusLabel(status) {
        return {
            changed: _t("Changed"), added: _t("Added"), removed: _t("Removed"), unchanged: _t("Unchanged"),
            unknown: _t("Not recorded"),
        }[status];
    }

    // ---- undo -------------------------------------------------------------

    async previewUndo() {
        const event = this.state.event;
        this.state.undo = await this.orm.call("ncollection.audit.undo", "nc_undo_preview", [event.id]);
        const selected = {};
        for (const row of this.state.undo.rows || []) {
            selected[row.field] = !!row.restorable;
        }
        this.state.undoSelected = selected;
    }
    get selectedFields() {
        return Object.keys(this.state.undoSelected).filter((k) => this.state.undoSelected[k]);
    }
    confirmUndo() {
        const undo = this.state.undo;
        const kind = undo.kind;
        if (kind === "update" && !this.selectedFields.length) {
            this.notification.add(_t("Choose at least one field to restore."), { type: "warning" });
            return;
        }
        const body = {
            update: _t("Restore %s field(s) of this document to their earlier values?", this.selectedFields.length),
            create: undo.modes[0] === "archive" ? _t("Archive the document this event created?")
                : _t("Delete the document this event created?"),
            delete: _t("Recreate the deleted document as a new document?"),
        }[kind];
        this.dialog.add(ConfirmationDialog, {
            title: _t("Confirm Undo"),
            body,
            confirmLabel: _t("Undo"),
            confirm: () => this.applyUndo(),
            cancel: () => {},
        });
    }
    async applyUndo() {
        this.state.undoBusy = true;
        try {
            const result = await this.orm.call("ncollection.audit.undo", "nc_undo_apply",
                [this.state.event.id, this.selectedFields, this.state.undo.fingerprint, this.state.undo.modes[0]]);
            this.state.undoDone = result;
            this.notification.add(_t("Undo done. It is recorded in the Audit Log."), { type: "success" });
            const id = this.state.event.id;
            this.state.event = await this.orm.call("ncollection.audit.service", "nc_audit_event", [id]);
            this.state.history = null;
            this.state.compare = null;
            this.state.undo = await this.orm.call("ncollection.audit.undo", "nc_undo_preview", [id]);
        } catch (error) {
            this.notification.add(error.data?.message || String(error), { type: "danger", sticky: true });
            await this.previewUndo();
        } finally {
            this.state.undoBusy = false;
        }
    }
}

registry.category("actions").add("ncollection_audit.audit_center", NcAuditCenter);
