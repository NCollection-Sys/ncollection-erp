/** @odoo-module **/

/**
 * Barcode Scanner: receive, deliver, transfer and count with a hardware scanner or the device camera.
 *
 * The screen holds NO stock logic. Every scan is one call to ncollection.stock.barcode (the server decides what a
 * barcode is, applies it under the user's own rights, and answers with the whole operation); this component
 * renders the answer. A hardware scanner is a keyboard that types the code and presses Enter, so one always-focused
 * input serves it; the camera uses Odoo's own scanner dialog.
 */

import { Component, onMounted, onWillStart, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { scanBarcode } from "@web/core/barcode/barcode_dialog";
import { isBarcodeScannerSupported } from "@web/core/barcode/barcode_video_scanner";
import { NcBadge, NcEmptyState } from "@ncollection_branding/components/components";

const SERVICE = "ncollection.stock.barcode";
const TYPE_LABEL = { incoming: _t("Receipt"), outgoing: _t("Delivery"), internal: _t("Transfer") };
const TYPE_BADGE = { incoming: "info", outgoing: "success", internal: "neutral" };

export class NcBarcodeScanner extends Component {
    static template = "ncollection_stock_barcode.Scanner";
    static props = ["*"];
    static components = { NcBadge, NcEmptyState };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.input = useRef("scan");
        this.cameraSupported = isBarcodeScannerSupported();
        this.texts = {
            nothingTitle: _t("Nothing to process"),
            nothingText: _t("There are no open operations right now."),
            chooseTitle: _t("Choose a location"),
            chooseText: _t("Scan the barcode of an internal location, then scan what you count."),
            countNote: _t("Applying adds up every counted quantity in this location, including counts entered elsewhere."),
        };
        this.state = useState({
            mode: "operations", // operations | picking | count
            filter: "",
            operations: [],
            picking: null,
            location: null, // the location a scan puts items into / takes them from
            needLot: null,
            count: null,
            countLocation: null,
            qty: 1,
            busy: false,
            message: null, // { text, level }
        });
        onWillStart(() => this.loadOperations());
        onMounted(() => this.focus());
    }

    // ------------------------------------------------------------------ labels
    typeLabel(code) {
        return TYPE_LABEL[code] || code;
    }
    typeBadge(code) {
        return TYPE_BADGE[code] || "neutral";
    }
    get hint() {
        if (this.state.mode === "count") {
            return this.state.count ? _t("Scan products to count them") : _t("Scan an internal location to start");
        }
        if (this.state.needLot) {
            return _t("Scan the lot / serial of %s", this.state.needLot.name);
        }
        return this.state.mode === "picking"
            ? _t("Scan a product, lot or location")
            : _t("Scan an operation reference, or pick one below");
    }

    // ------------------------------------------------------------------ plumbing
    focus() {
        this.input.el && this.input.el.focus();
    }

    say(text, level = "info") {
        this.state.message = { text, level };
    }

    async call(method, args = [], kwargs = {}) {
        this.state.busy = true;
        try {
            return await this.orm.call(SERVICE, method, args, kwargs);
        } catch (error) {
            // A refusal (rights, a vanished record) is shown, never swallowed.
            const text = (error.data && error.data.message) || error.message || _t("The server refused this scan.");
            this.say(text, "danger");
            return null;
        } finally {
            this.state.busy = false;
            this.focus();
        }
    }

    // ------------------------------------------------------------------ operations list
    async loadOperations() {
        const rows = await this.call("nc_barcode_operations", [this.state.filter || null]);
        this.state.operations = rows || [];
    }

    async setFilter(code) {
        this.state.filter = code;
        await this.loadOperations();
    }

    async openPicking(id) {
        const reply = await this.call("nc_barcode_open", [id]);
        if (reply) {
            this.state.mode = "picking";
            this.state.picking = reply.state;
            this.state.location = null;
            this.state.needLot = null;
            this.say(reply.message, reply.level);
        }
    }

    async backToList() {
        this.state.mode = "operations";
        this.state.picking = null;
        this.state.needLot = null;
        await this.loadOperations();
        this.focus();
    }

    // ------------------------------------------------------------------ scanning
    /** Scans are queued, never dropped: a hardware scanner can fire the next code before the server answered. */
    onScan(code) {
        const barcode = (code || "").trim();
        if (!barcode) {
            return Promise.resolve();
        }
        this._queue = (this._queue || Promise.resolve()).then(() => this.processScan(barcode));
        return this._queue;
    }

    async processScan(barcode) {
        if (this.state.mode === "count") {
            return this.scanCount(barcode);
        }
        if (this.state.mode === "operations") {
            return this.scanForOperation(barcode);
        }
        return this.scanPicking(barcode);
    }

    async scanForOperation(barcode) {
        const match = this.state.operations.find((op) => op.name === barcode);
        if (match) {
            return this.openPicking(match.id);
        }
        this.say(_t("%s is not an open operation.", barcode), "danger");
    }

    async scanPicking(barcode) {
        const kwargs = { qty: this.state.qty || 1 };
        if (this.state.needLot) {
            kwargs.lot_for_product_id = this.state.needLot.product_id;
        }
        if (this.state.location) {
            kwargs.location_id = this.state.location.id;
        }
        const reply = await this.call("nc_barcode_scan", [this.state.picking.id, barcode], kwargs);
        if (!reply) {
            return;
        }
        this.say(reply.message, reply.level);
        if (reply.switch_to) {
            return this.openPicking(reply.switch_to);
        }
        if (reply.state) {
            this.state.picking = reply.state;
        }
        if (reply.location) {
            this.state.location = reply.location;
        }
        this.state.needLot = reply.need_lot || (reply.ok ? null : this.state.needLot);
        if (reply.ok && !reply.need_lot) {
            this.state.qty = 1;
        }
    }

    async adjust(line, delta) {
        const reply = await this.call("nc_barcode_adjust", [this.state.picking.id, line.move_id, delta]);
        if (reply) {
            this.state.picking = reply.state;
            this.say(reply.message, reply.level);
        }
    }

    clearLocation() {
        this.state.location = null;
        this.focus();
    }

    async validate() {
        const reply = await this.call("nc_barcode_validate", [this.state.picking.id]);
        if (!reply) {
            return;
        }
        if (reply.done) {
            this.say(reply.message, "success");
            return this.backToList();
        }
        // Odoo needs a decision (backorder, ...): its own wizard, then refresh this operation.
        await this.action.doAction(reply.action, {
            onClose: () => this.openPicking(this.state.picking.id),
        });
    }

    async camera() {
        try {
            const code = await scanBarcode(this.env);
            await this.onScan(code);
        } catch (error) {
            this.say((error && error.message) || _t("The camera could not be used."), "danger");
        }
    }

    onKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            const code = ev.target.value;
            ev.target.value = "";
            this.onScan(code);
        }
    }

    // ------------------------------------------------------------------ inventory count
    async showCount() {
        this.state.mode = "count";
        this.state.count = null;
        this.say(_t("Scan an internal location to start counting."), "info");
        this.focus();
    }

    async scanCount(barcode) {
        if (!this.state.count) {
            const result = await this.call("nc_barcode_count_open", [barcode]);
            if (result) {
                this.state.count = result;
                this.say(_t("Counting %s", result.location.name), "info");
            }
            return;
        }
        const kwargs = { qty: this.state.qty || 1 };
        if (this.state.needLot) {
            kwargs.lot_for_product_id = this.state.needLot.product_id;
        }
        const reply = await this.call("nc_barcode_count_scan", [this.state.count.location.id, barcode], kwargs);
        if (reply) {
            this.state.count = { location: reply.location, rows: reply.rows };
            this.state.needLot = reply.need_lot || null;
            this.say(reply.message, reply.level);
            if (reply.ok && !reply.need_lot) {
                this.state.qty = 1;
            }
        }
    }

    async applyCount() {
        const reply = await this.call("nc_barcode_count_apply", [this.state.count.location.id]);
        if (!reply) {
            return;
        }
        this.state.count = { location: reply.location, rows: reply.rows };
        if (reply.action) {
            // Odoo needs a decision first: its own wizard, then show what is still pending.
            this.say(reply.message, "info");
            await this.action.doAction(reply.action, {
                onClose: async () => {
                    const fresh = await this.call("nc_barcode_count_state", [reply.location.id]);
                    if (fresh) {
                        this.state.count = fresh;
                    }
                },
            });
            return;
        }
        this.say(reply.message, "success");
    }
}

registry.category("actions").add("ncollection_stock_barcode.scanner", NcBarcodeScanner);
