/** @odoo-module **/
// Invoice line columns: free ORDER and VISIBILITY, a company default plus a per-user override.
//
// A maintainable extension of Odoo's own ListRenderer (patch, not a fork): it only changes WHICH columns are active and in
// WHAT order (getActiveColumns), where the optional-column choice is remembered (computeOptionalActiveFields /
// saveOptionalActiveFields), and adds one entry to the existing column menu. Cells, inline editing, onchange, keyboard
// navigation and every other list feature are Odoo's own and untouched. Applies only to the invoice-lines list
// (account.move.line carrying the Discount column), i.e. customer invoices and vendor bills alike.
import { Component, onWillStart, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { Dialog } from "@web/core/dialog/dialog";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { ListRenderer } from "@web/views/list/list_renderer";

const LIST_KEY = "invoice_lines";
const MODEL = "ncollection.invoice.column.pref";

export class NcColumnArrangeDialog extends Component {
    static template = "ncollection_account_invoice_layout.ColumnArrangeDialog";
    static components = { Dialog };
    static props = {
        rows: Array,
        canCompany: Boolean,
        source: String,
        onSave: Function,
        onReset: Function,
        close: Function,
    };

    setup() {
        this.state = useState({ rows: this.props.rows.map((r) => ({ ...r })), over: null });
        this.dragIndex = null; // not reactive: re-rendering during dragstart would cancel the browser's drag
    }
    get title() {
        return _t("Arrange invoice columns");
    }
    move(index, delta) {
        const target = index + delta;
        const rows = this.state.rows;
        if (target < 0 || target >= rows.length) {
            return;
        }
        [rows[index], rows[target]] = [rows[target], rows[index]];
    }
    onDragStart(ev, index) {
        this.dragIndex = index;
        ev.dataTransfer.effectAllowed = "move";
        ev.dataTransfer.setData("text/plain", String(index));
    }
    onDragOver(ev) {
        ev.preventDefault();
    }
    onDrop(ev, index) {
        ev.preventDefault();
        const from = this.dragIndex;
        this.dragIndex = null;
        this.state.over = null;
        if (from === null || from === index) {
            return;
        }
        const [row] = this.state.rows.splice(from, 1);
        this.state.rows.splice(index, 0, row);
    }
    onToggle(row, ev) {
        row.visible = ev.target.checked;
    }
    get payload() {
        return {
            order: this.state.rows.map((r) => r.name),
            hidden: this.state.rows.filter((r) => !r.visible).map((r) => r.name),
        };
    }
    async save(scope) {
        await this.props.onSave(this.payload, scope);
        this.props.close();
    }
    async reset() {
        await this.props.onReset();
        this.props.close();
    }
}

let prefCache = null; // one request per page load; replaced after every save

patch(ListRenderer.prototype, {
    setup() {
        super.setup(...arguments);
        this.ncPref = null;
        this.ncDialogService = useService("dialog");
        if (this.ncIsInvoiceLines) {
            onWillStart(async () => {
                this.ncPref = await this.ncLoadPref();
            });
        }
    },

    get ncIsInvoiceLines() {
        const info = this.props.archInfo;
        return (
            this.props.list.resModel === "account.move.line" &&
            info.columns.some((col) => col.type === "field" && col.name === "nc_discount_amount")
        );
    },

    async ncLoadPref() {
        if (!prefCache) {
            prefCache = this.orm
                .call(MODEL, "nc_get", [LIST_KEY])
                .catch(() => ({ order: [], hidden: [], has_hidden: false, source: "view", can_company: false }));
        }
        return prefCache;
    },

    /** Reorder only the movable field columns (the ones the preference names), into the slots they already occupy:
     * handle / button / unnamed columns keep their place. */
    ncApplyOrder(columns) {
        const order = (this.ncPref && this.ncPref.order) || [];
        if (!order.length) {
            return columns;
        }
        const movable = (col) => col.type === "field" && order.includes(col.name);
        const picked = columns.filter(movable).sort((a, b) => order.indexOf(a.name) - order.indexOf(b.name));
        let i = 0;
        return columns.map((col) => (movable(col) ? picked[i++] : col));
    },

    getActiveColumns() {
        const columns = super.getActiveColumns(...arguments);
        return this.ncIsInvoiceLines ? this.ncApplyOrder(columns) : columns;
    },

    computeOptionalActiveFields() {
        const active = super.computeOptionalActiveFields(...arguments);
        if (this.ncIsInvoiceLines && this.ncPref && this.ncPref.source !== "view" && this.ncPref.has_hidden) {
            for (const name of Object.keys(active)) {
                active[name] = !this.ncPref.hidden.includes(name);
            }
        }
        return active;
    },

    /** The column menu's check boxes are saved as the user's own preference (server side), not only in this browser. */
    saveOptionalActiveFields() {
        super.saveOptionalActiveFields(...arguments);
        if (this.ncIsInvoiceLines && this.ncPref) {
            const hidden = this.allColumns
                .filter((col) => col.optional && !this.optionalActiveFields[col.name])
                .map((col) => col.name);
            this.ncSave({ order: this.ncPref.order, hidden }, "user");
        }
    },

    async ncSave({ order, hidden }, scope) {
        prefCache = this.orm.call(MODEL, "nc_save", [LIST_KEY, order, hidden, scope]);
        this.ncPref = await prefCache;
        this.render(true);
    },

    async ncReset() {
        prefCache = this.orm.call(MODEL, "nc_reset", [LIST_KEY]);
        this.ncPref = await prefCache;
        this.render(true);
    },

    /** Every arrangeable column in its current order. */
    ncArrangeRows() {
        const cols = this.allColumns.filter(
            (col) =>
                col.type === "field" && col.widget !== "handle" && !this.evalColumnInvisible(col.column_invisible)
        );
        return this.ncApplyOrder(cols).map((col) => ({
            name: col.name,
            label: col.label || col.name,
            optional: !!col.optional,
            visible: !col.optional || !!this.optionalActiveFields[col.name],
        }));
    },

    ncOpenArrange() {
        this.ncDialogService.add(NcColumnArrangeDialog, {
            rows: this.ncArrangeRows(),
            canCompany: !!(this.ncPref && this.ncPref.can_company),
            source: (this.ncPref && this.ncPref.source) || "view",
            onSave: (payload, scope) => this.ncSave(payload, scope),
            onReset: () => this.ncReset(),
        });
    },
});
