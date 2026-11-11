/** @odoo-module **/
// The invoice's Invoice Lines table shows the COMMERCIAL lines only.
//
// A charge or discount of the Charges & Adjustments block is booked as a real journal item (its account, its taxes),
// and that item is part of the invoice's lines on the server: taxes, totals, the amount audit and the e-invoice all
// read it there. It carries an explicit link, account.move.line.nc_adjustment_id, which the invoice form loads as a
// hidden column. For that list only (the account.move.line list that loads nc_adjustment_id), the list renderer sees a
// view of its list whose `records` leaves the linked lines out: the rows, the footer sums, the keyboard navigation and
// the empty state all use it, while every operation (edit, add, delete, resequence, save) runs on Odoo's own list, so
// nothing is removed from the invoice and no command changes. It is data, not DOM: the rule is applied on every render,
// after a reload, an onchange or a save. The lines stay visible where they belong: Charges & Adjustments, Journal Items.
//
// The view wraps the renderer's OWN props.list (the reactive Owl gives this component), so the renderer keeps exactly
// the reactivity it has without the patch. Wrapping the field's list instead (one level up) subscribed the wrong
// component and rendered forever.
import { patch } from "@web/core/utils/patch";
import { ListRenderer } from "@web/views/list/list_renderer";

const views = new WeakMap();
const shownProps = new WeakMap();

export function isAdjustmentLine(record) {
    return Boolean(record.data && record.data.nc_adjustment_id);
}

function sameRecords(a, b) {
    return a.length === b.length && a.every((record, index) => record === b[index]);
}

export function commercialView(list) {
    let view = views.get(list);
    if (!view) {
        // the SAME array while the visible rows are the same: the renderer compares `records` between renders
        let last = [];
        view = new Proxy(list, {
            get(target, prop) {
                if (prop === "records") {
                    const visible = target.records.filter((record) => !isAdjustmentLine(record));
                    if (!sameRecords(visible, last)) {
                        last = visible;
                    }
                    return last;
                }
                const value = Reflect.get(target, prop);
                return typeof value === "function" ? value.bind(target) : value;
            },
        });
        views.set(list, view);
    }
    return view;
}

function isInvoiceLinesList(props) {
    const list = props && props.list;
    return Boolean(
        list && list.resModel === "account.move.line" && list.activeFields && "nc_adjustment_id" in list.activeFields
    );
}

function withCommercialList(props) {
    if (!isInvoiceLinesList(props)) {
        return props;
    }
    let shown = shownProps.get(props);
    if (!shown) {
        shown = { ...props, list: commercialView(props.list) };
        shownProps.set(props, shown);
    }
    return shown;
}

patch(ListRenderer.prototype, {
    setup() {
        if (isInvoiceLinesList(this.props)) {
            // Owl assigns `props` on every update: keep the assigned value and hand the renderer its commercial view
            let assigned = this.props;
            Object.defineProperty(this, "props", {
                configurable: true,
                get: () => withCommercialList(assigned),
                set: (value) => {
                    assigned = value;
                },
            });
        }
        super.setup(...arguments);
    },
});
