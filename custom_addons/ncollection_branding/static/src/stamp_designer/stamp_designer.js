/** @odoo-module **/
import { Component, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

const MAP_W = 250; // px, the width the page-shaped control is drawn at
const MAP_H = 330; // px, and the most height it may take
const PREVIEW_DELAY = 700; // ms of quiet after the last change before the real PDF is asked again
const COMMIT_DELAY = 150; // ms of quiet before a burst of edits (zoom clicks, a drag) reaches the record
const GEOMETRY = ["x_mm", "y_mm", "width_mm", "height_mm"];

/**
 * Stamp designer: a CONTROL for the position and size of a stamp, and the REAL preview next to it.
 *
 *  - The page-shaped box is only a way to set four numbers (from the left, from the top, width, height, all in mm) by
 *    dragging: it is drawn from the paper size in mm and never claims to show the document.
 *  - The document you check is the PDF below it, produced by the server with the very engine that stamps a printed
 *    document, at the numbers as they are right now (not yet saved). Dragging, nudging or typing refreshes it.
 * Nothing here knows how a stamp is drawn; it only edits the record's numbers.
 *
 * Responsiveness: every record.update() is a server onchange, and the box used to carry the image (base64 for a new
 * file) in its style, so each zoom click or pointer move cost an RPC plus a re-decode of the image. Edits now land in a
 * local DRAFT that the control draws at once; the record gets the result once per burst (after COMMIT_DELAY, at the
 * end of a drag, and before the widget goes away), and the image is a stable <img> whose source does not change.
 */
export class NcStampDesigner extends Component {
    static template = "ncollection_branding.StampDesigner";
    static props = { ...standardWidgetProps };

    setup() {
        this.state = useState({ previewUrl: this.buildUrl() });
        this.draft = useState({});
        this._timer = null;
        this._commitTimer = null;
        this._drag = null;
        this._image = { key: null, url: null };
        onWillUnmount(() => {
            clearTimeout(this._timer);
            this.commit();
            if (this._image.url && this._image.url.startsWith("blob:")) {
                URL.revokeObjectURL(this._image.url);
            }
        });
    }

    /** The record's values with the edits not yet committed on top: what the control draws. */
    get data() {
        return Object.assign({}, this.props.record.data, this.draft);
    }

    get canPreview() {
        return !!(this.props.record.resId && this.data.image);
    }

    get sheet() {
        return [this.data.sheet_width_mm || 210, this.data.sheet_height_mm || 297];
    }

    get scale() {
        const [w, h] = this.sheet;
        return Math.min(MAP_W / w, MAP_H / h);
    }

    get pageStyle() {
        const [w, h] = this.sheet;
        const grid = 10 * this.scale;
        return (
            `width:${(w * this.scale).toFixed(1)}px;height:${(h * this.scale).toFixed(1)}px;` +
            `background-size:${grid.toFixed(2)}px ${grid.toFixed(2)}px;`
        );
    }

    get boxStyle() {
        const d = this.data;
        const s = this.scale;
        return (
            `left:${(d.x_mm * s).toFixed(1)}px;top:${(d.y_mm * s).toFixed(1)}px;` +
            `width:${(d.width_mm * s).toFixed(1)}px;height:${(d.height_mm * s).toFixed(1)}px;`
        );
    }

    get imageSrc() {
        const image = this.props.record.data.image;
        // a freshly picked file is base64 in the record (decoded ONCE into an object URL); a stored one is fetched by
        // the standard image route. The source only changes when the image does.
        const fresh = image && image.length > 64;
        const key = fresh ? `${image.length}:${image.slice(0, 32)}:${image.slice(-32)}` : "stored";
        if (this._image.key !== key) {
            if (this._image.url && this._image.url.startsWith("blob:")) {
                URL.revokeObjectURL(this._image.url);
            }
            let url = `/web/image/ncollection.document.stamp/${this.props.record.resId}/image`;
            if (fresh) {
                const bytes = Uint8Array.from(atob(image), (c) => c.charCodeAt(0));
                url = URL.createObjectURL(new Blob([bytes], { type: "image/png" }));
            }
            this._image = { key, url };
        }
        return this._image.url;
    }

    get sizeLabel() {
        const d = this.data;
        return _t("%(w)s × %(h)s mm, %(x)s mm from the left, %(y)s mm from the top", {
            w: d.width_mm,
            h: d.height_mm,
            x: d.x_mm,
            y: d.y_mm,
        });
    }

    buildUrl() {
        const d = this.data;
        if (!this.props.record.resId) {
            return "";
        }
        const q = new URLSearchParams({
            x: d.x_mm,
            y: d.y_mm,
            w: d.width_mm,
            h: d.height_mm,
            pages: d.page_scope || "last",
            _: Date.now(),
        });
        return `/ncollection/stamp/preview/${this.props.record.resId}?${q}#toolbar=0&navpanes=0&view=FitH`;
    }

    schedulePreview() {
        clearTimeout(this._timer);
        this._timer = setTimeout(() => (this.state.previewUrl = this.buildUrl()), PREVIEW_DELAY);
    }

    round(value) {
        return Math.round(value * 10) / 10;
    }

    set(values) {
        Object.assign(this.draft, values);
        clearTimeout(this._commitTimer);
        this._commitTimer = setTimeout(() => this.commit(), COMMIT_DELAY);
    }

    /** Hand the drafted numbers to the record in ONE update (one onchange), then refresh the real preview. */
    async commit() {
        clearTimeout(this._commitTimer);
        const values = {};
        for (const name of GEOMETRY) {
            if (name in this.draft) {
                values[name] = this.draft[name];
            }
        }
        if (!Object.keys(values).length) {
            return;
        }
        await this.props.record.update(values);
        for (const name of Object.keys(values)) {
            if (this.draft[name] === values[name]) {
                delete this.draft[name];
            }
        }
        this.schedulePreview();
    }

    // ---- dragging in the page-shaped control (mm = px / scale) --------------------------------------------------
    onPointerDown(ev, mode) {
        ev.preventDefault();
        ev.stopPropagation();
        const d = this.data;
        this._drag = { mode, sx: ev.clientX, sy: ev.clientY, x: d.x_mm, y: d.y_mm, w: d.width_mm, h: d.height_mm };
        ev.currentTarget.setPointerCapture(ev.pointerId);
    }

    onPointerMove(ev) {
        const drag = this._drag;
        if (!drag) {
            return;
        }
        const dx = (ev.clientX - drag.sx) / this.scale;
        const dy = (ev.clientY - drag.sy) / this.scale;
        if (drag.mode === "move") {
            this.set({ x_mm: Math.max(0, this.round(drag.x + dx)), y_mm: Math.max(0, this.round(drag.y + dy)) });
        } else {
            const width = Math.max(1, this.round(drag.w + dx));
            const ratio = this.data.aspect_ratio;
            const height = this.data.keep_ratio && ratio ? this.round(width / ratio) : Math.max(1, this.round(drag.h + dy));
            this.set({ width_mm: width, height_mm: height });
        }
    }

    onPointerUp() {
        if (this._drag) {
            this._drag = null;
            this.commit();
        }
    }

    // ---- buttons ----------------------------------------------------------------------------------------------
    nudge(dx, dy, ev) {
        const step = ev && ev.shiftKey ? 5 : 1;
        this.set({
            x_mm: Math.max(0, this.round(this.data.x_mm + dx * step)),
            y_mm: Math.max(0, this.round(this.data.y_mm + dy * step)),
        });
    }

    resize(factor) {
        const d = this.data;
        const width = Math.max(1, this.round(d.width_mm * factor));
        const ratio = d.aspect_ratio;
        this.set({ width_mm: width, height_mm: d.keep_ratio && ratio ? this.round(width / ratio) : Math.max(1, this.round(d.height_mm * factor)) });
    }

    async refresh() {
        await this.commit();
        clearTimeout(this._timer);
        this.state.previewUrl = this.buildUrl();
    }

    printTest() {
        // a direct reaction to the click, so the browser does not treat the new tab as a pop-up
        window.open(this.buildUrl().split("#")[0], "_blank");
        this.commit();
    }
}

registry.category("view_widgets").add("nc_stamp_designer", {
    component: NcStampDesigner,
    fieldDependencies: [
        { name: "x_mm", type: "float" },
        { name: "y_mm", type: "float" },
        { name: "width_mm", type: "float" },
        { name: "height_mm", type: "float" },
        { name: "page_scope", type: "selection" },
        { name: "keep_ratio", type: "boolean" },
        { name: "aspect_ratio", type: "float" },
        { name: "sheet_width_mm", type: "float" },
        { name: "sheet_height_mm", type: "float" },
        { name: "image", type: "binary" },
    ],
});
