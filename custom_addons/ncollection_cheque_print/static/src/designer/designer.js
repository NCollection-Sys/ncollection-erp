/** @odoo-module **/
/*
 * The cheque template designer: a focused drag-and-drop editor, not a general design tool.
 *
 * ONE UNIT. Everything the user sees and types is millimetres from the top-left corner of the cheque, because that is
 * what the PDF is built from. The canvas converts to screen pixels with a single scale (pixels per millimetre) that only
 * affects how it is DRAWN; nothing in a pixel is ever stored.
 *
 * TWO VIEWS. This canvas is the DESIGN view: the reference image (a scan of the cheque) behind boxes showing where each
 * field prints. It is never what is printed. The Print preview and Calibration sheet buttons open the real PDF, which
 * contains the variable text only.
 *
 * EDITS ARE SAVED AS THEY HAPPEN. The designer writes the template's field records directly (debounced), so there is no
 * second copy of the layout and nothing to forget to save. The server validates every write (a field cannot leave the
 * page); a refused write reloads the layout and says why.
 */
import { Component, onMounted, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

const FIELD_MODEL = "ncollection.cheque.template.field";
const round1 = (value) => Math.round(value * 10) / 10;
const clamp = (value, low, high) => Math.min(Math.max(value, low), Math.max(low, high));
const PT_TO_MM = 25.4 / 72;

export class NcChequeDesigner extends Component {
    static template = "ncollection_cheque_print.Designer";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.canvasHost = useRef("host");
        this.state = useState({
            loaded: false,
            tpl: null,
            options: null,
            fields: [],
            selectedId: null,
            newKey: "payee",
            status: "idle",
            showImage: true,
            showGrid: true,
            scale: 4,
        });
        this.pending = {};
        this.timers = {};
        onWillStart(() => this.load());
        onMounted(() => {
            this.measure();
            this.observer = new ResizeObserver(() => this.measure());
            if (this.canvasHost.el) {
                this.observer.observe(this.canvasHost.el);
            }
        });
        onWillUnmount(() => {
            this.observer?.disconnect();
            Object.values(this.timers).forEach((timer) => clearTimeout(timer));
        });
    }

    // ------------------------------------------------------------------ data
    async load() {
        const id = this.props.record.resId;
        if (!id) {
            return;
        }
        const data = await this.orm.call("ncollection.cheque.template", "designer_load", [[id]]);
        this.state.tpl = data;
        this.state.options = data.options;
        this.state.fields = data.fields;
        this.state.loaded = true;
        if (!this.state.fields.some((field) => field.id === this.state.selectedId)) {
            this.state.selectedId = this.state.fields.length ? this.state.fields[0].id : null;
        }
    }

    measure() {
        const host = this.canvasHost.el;
        if (host && this.state.tpl) {
            const available = host.clientWidth - 2;
            this.state.scale = clamp(available / this.state.tpl.width_mm, 1.5, 6);
        }
    }

    get scale() {
        return this.state.scale;
    }

    get selected() {
        return this.state.fields.find((field) => field.id === this.state.selectedId) || null;
    }

    get imageUrl() {
        const tpl = this.state.tpl;
        return tpl && tpl.has_image
            ? `/web/image/ncollection.cheque.template/${tpl.id}/reference_image?unique=${encodeURIComponent(tpl.stamp)}`
            : "";
    }

    get canvasStyle() {
        const tpl = this.state.tpl;
        const gridPx = 10 * this.scale;
        const grid = this.state.showGrid
            ? `background-image: linear-gradient(to right, rgba(31,95,143,.12) 1px, transparent 1px),` +
              `linear-gradient(to bottom, rgba(31,95,143,.12) 1px, transparent 1px); background-size: ${gridPx}px ${gridPx}px;`
            : "";
        return `width:${tpl.width_mm * this.scale}px; height:${tpl.height_mm * this.scale}px; ${grid}`;
    }

    boxStyle(field) {
        const s = this.scale;
        const fontPx = field.size_pt * PT_TO_MM * s;
        return (
            `inset-inline-start:${field.x_mm * s}px; inset-block-start:${field.y_mm * s}px;` +
            `inline-size:${field.width_mm * s}px; block-size:${field.height_mm * s}px;` +
            `font-size:${fontPx}px; text-align:${field.align === "auto" ? "start" : field.align};` +
            `font-weight:${field.bold ? 700 : 400}; direction:${field.direction === "rtl" ? "rtl" : "ltr"};` +
            `white-space:${field.fit === "wrap" ? "normal" : "nowrap"};`
        );
    }

    /** Automatic direction: the paragraph direction follows the first strong letter, as the PDF does. */
    textStyle(field) {
        return field.direction === "auto" ? "unicode-bidi:plaintext;" : "";
    }

    label(field) {
        const found = this.state.options.keys.find(([key]) => key === field.key);
        const base = found ? found[1] : field.key;
        return field.key === "static_text" && field.static_text ? `${base}: ${field.static_text}` : base;
    }

    sample(field) {
        const samples = this.state.tpl.samples;
        let text = field.key === "static_text" ? field.static_text || "" : samples[field.key] || "";
        if (field.key === "amount_words") {
            text = {
                ar: samples.amount_words_ar,
                en: samples.amount_words,
                auto: samples.amount_words_auto,
            }[field.words_lang] || samples.amount_words;
        }
        if (field.case === "upper") {
            text = text.toUpperCase();
        } else if (field.case === "lower") {
            text = text.toLowerCase();
        }
        return `${field.prefix || ""}${text}${field.suffix || ""}`;
    }

    // ------------------------------------------------------------------ selection and persistence
    select(id) {
        this.state.selectedId = id;
    }

    persist(field, names) {
        const pending = (this.pending[field.id] ||= {});
        names.forEach((name) => {
            pending[name] = field[name];
        });
        this.state.status = "saving";
        clearTimeout(this.timers[field.id]);
        this.timers[field.id] = setTimeout(() => this.flush(field.id), 350);
    }

    async flush(id) {
        const values = this.pending[id];
        delete this.pending[id];
        if (!values) {
            return;
        }
        try {
            await this.orm.write(FIELD_MODEL, [id], values);
            this.state.status = Object.keys(this.pending).length ? "saving" : "saved";
        } catch (error) {
            this.state.status = "error";
            this.notification.add(error?.data?.message || _t("The change was refused."), { type: "danger" });
            await this.load();
        }
    }

    setProp(field, name, raw) {
        let value = raw;
        if (["x_mm", "y_mm", "width_mm", "height_mm", "size_pt"].includes(name)) {
            value = parseFloat(raw);
            if (Number.isNaN(value)) {
                return;
            }
        } else if (name === "max_lines") {
            value = Math.max(1, Math.min(6, parseInt(raw, 10) || 1));
        } else if (name === "bold" || name === "visible") {
            value = !!raw;
        }
        field[name] = value;
        this.persist(field, [name]);
    }

    // ------------------------------------------------------------------ drag, resize, nudge
    startMove(ev, field) {
        if (ev.button !== 0) {
            return;
        }
        ev.preventDefault();
        this.select(field.id);
        const start = { x: ev.clientX, y: ev.clientY, fx: field.x_mm, fy: field.y_mm };
        const tpl = this.state.tpl;
        const move = (e) => {
            field.x_mm = clamp(round1(start.fx + (e.clientX - start.x) / this.scale), 0, tpl.width_mm - field.width_mm);
            field.y_mm = clamp(round1(start.fy + (e.clientY - start.y) / this.scale), 0, tpl.height_mm - field.height_mm);
        };
        const up = () => {
            window.removeEventListener("pointermove", move);
            this.persist(field, ["x_mm", "y_mm"]);
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up, { once: true });
    }

    startResize(ev, field) {
        if (ev.button !== 0) {
            return;
        }
        ev.preventDefault();
        ev.stopPropagation();
        this.select(field.id);
        const start = { x: ev.clientX, y: ev.clientY, w: field.width_mm, h: field.height_mm };
        const tpl = this.state.tpl;
        const move = (e) => {
            field.width_mm = clamp(round1(start.w + (e.clientX - start.x) / this.scale), 3, tpl.width_mm - field.x_mm);
            field.height_mm = clamp(round1(start.h + (e.clientY - start.y) / this.scale), 2, tpl.height_mm - field.y_mm);
        };
        const up = () => {
            window.removeEventListener("pointermove", move);
            this.persist(field, ["width_mm", "height_mm"]);
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up, { once: true });
    }

    onKeydown(ev, field) {
        const step = ev.shiftKey ? 1 : 0.1;
        const tpl = this.state.tpl;
        const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
        if (moves[ev.key]) {
            ev.preventDefault();
            field.x_mm = clamp(round1(field.x_mm + moves[ev.key][0]), 0, tpl.width_mm - field.width_mm);
            field.y_mm = clamp(round1(field.y_mm + moves[ev.key][1]), 0, tpl.height_mm - field.height_mm);
            this.persist(field, ["x_mm", "y_mm"]);
        } else if (ev.key === "Delete" || ev.key === "Backspace") {
            if (ev.target === ev.currentTarget) {
                ev.preventDefault();
                this.removeField(field);
            }
        }
    }

    // ------------------------------------------------------------------ add / remove
    async addField() {
        const key = this.state.newKey;
        if (key !== "static_text" && this.state.fields.some((field) => field.key === key)) {
            this.notification.add(_t("That field is already on the cheque. Select it and move it instead."), {
                type: "warning",
            });
            return;
        }
        const box = this.state.tpl.defaults[key] || { width_mm: 50, height_mm: 7, size_pt: 10 };
        const tpl = this.state.tpl;
        const values = {
            template_id: tpl.id,
            key,
            x_mm: clamp(10, 0, tpl.width_mm - box.width_mm),
            y_mm: clamp(10, 0, tpl.height_mm - box.height_mm),
            width_mm: Math.min(box.width_mm, tpl.width_mm),
            height_mm: Math.min(box.height_mm, tpl.height_mm),
            size_pt: box.size_pt,
            sequence: (this.state.fields.length + 1) * 10,
        };
        if (key === "static_text") {
            values.static_text = _t("Text");
        }
        try {
            const [id] = await this.orm.create(FIELD_MODEL, [values]);
            await this.load();
            this.state.selectedId = id;
            this.state.status = "saved";
        } catch (error) {
            this.notification.add(error?.data?.message || _t("The field could not be added."), { type: "danger" });
        }
    }

    async removeField(field) {
        await this.orm.unlink(FIELD_MODEL, [field.id]);
        this.state.selectedId = null;
        await this.load();
    }

    // ------------------------------------------------------------------ the real PDFs
    openPdf(mode) {
        const id = this.state.tpl.id;
        window.open(`/ncollection/cheque/preview?template=${id}&mode=${mode}`, "_blank");
    }

    get statusLabel() {
        return { idle: "", saving: _t("Saving…"), saved: _t("All changes saved"), error: _t("Not saved") }[this.state.status];
    }
}

registry.category("view_widgets").add("nc_cheque_designer", { component: NcChequeDesigner });
