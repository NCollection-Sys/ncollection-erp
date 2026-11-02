/** @odoo-module **/
/**
 * Product image on document lines (LINE-04): a small image button next to the product of an invoice, vendor bill,
 * sales order or purchase order line - only when that product (the selected VARIANT) has an image - and a preview
 * dialog on click.
 *
 * One patch covers all four: the purchase and invoice lines use account's ProductLabelSectionAndNoteField, and sale's
 * SaleOrderLineProductField extends it (its template inherits the account one).
 *
 * Cost: whether a product has an image is read ONCE per product for the whole page, in one batched read with
 * bin_size (the image's size, never its bytes) for every line rendered together - no request per line. The image
 * itself is only fetched when the preview opens, through /web/image, which applies the product's own access rules.
 */
import { Component, onWillStart, onWillUpdateProps, useState } from "@odoo/owl";
import { ProductLabelSectionAndNoteField } from "@account/components/product_label_section_and_note_field/product_label_section_and_note_field";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";

const known = new Map(); // product.product id -> Promise<boolean>
let queue = null;

function hasImage(orm, productId) {
    if (!known.has(productId)) {
        if (!queue) {
            queue = new Map();
            // every line rendered in the same tick joins one read
            Promise.resolve().then(() => {
                const batch = queue;
                queue = null;
                const ids = [...batch.keys()];
                orm.read("product.product", ids, ["image_128"], { context: { bin_size: true } }).then(
                    (rows) => {
                        const withImage = new Set(rows.filter((row) => row.image_128).map((row) => row.id));
                        for (const [id, resolve] of batch) {
                            resolve(withImage.has(id));
                        }
                    },
                    () => {
                        for (const [id, resolve] of batch) {
                            known.delete(id); // a failed read is retried on the next render, never cached as "no"
                            resolve(false);
                        }
                    }
                );
            });
        }
        known.set(productId, new Promise((resolve) => queue.set(productId, resolve)));
    }
    return known.get(productId);
}

export class NcProductImageDialog extends Component {
    static template = "ncollection_account_core.ProductImageDialog";
    static components = { Dialog };
    static props = { productId: Number, title: String, close: Function };

    get src() {
        return `/web/image/product.product/${this.props.productId}/image_1024`;
    }
}

patch(ProductLabelSectionAndNoteField.prototype, {
    setup() {
        super.setup(...arguments);
        this.ncOrm = useService("orm");
        this.ncDialog = useService("dialog");
        this.ncImage = useState({ productId: false });
        onWillStart(() => this.ncCheckImage(this.props));
        onWillUpdateProps((next) => this.ncCheckImage(next));
    },

    ncProductIdOf(props) {
        const value = props.record.data[props.name];
        return value && value.id;
    },

    ncCheckImage(props) {
        const productId = this.ncProductIdOf(props);
        if (!productId) {
            this.ncImage.productId = false;
            return;
        }
        // not awaited: the line renders at once and the button appears when the (batched) answer arrives
        hasImage(this.ncOrm, productId).then((yes) => {
            if (this.ncProductIdOf(this.props) === productId) {
                this.ncImage.productId = yes ? productId : false;
            }
        });
    },

    get ncImageLabel() {
        return _t("Show the product image");
    },

    ncOpenImage(ev) {
        ev.stopPropagation();
        const value = this.props.record.data[this.props.name];
        this.ncDialog.add(NcProductImageDialog, {
            productId: this.ncImage.productId,
            title: (value && value.display_name) || _t("Product image"),
        });
    },
});
