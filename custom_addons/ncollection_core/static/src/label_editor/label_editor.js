/** @odoo-module **/
// In-page label editor (Settings > Interface Text). While "Edit labels" is on, a click on a supported element opens a
// small dialog instead of acting. The element is IDENTIFIED from Odoo's own metadata (field name, menu id, button text
// within the current model) and the change is saved through ncollection.label.override.nc_quick_edit, which validates,
// checks rights and the platform policy, and audits. The page is then reloaded: Odoo itself renders the new text. No
// text is ever replaced in the DOM, and the value is plain text (the server refuses markup).
import { Component, useState, onWillStart } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";

const BODY_CLASS = "o_nc_label_edit";

export class NcLabelDialog extends Component {
    static template = "ncollection_core.NcLabelDialog";
    static components = { Dialog };
    static props = ["target", "close"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ value: this.props.target.text, scope: "workspace", busy: false });
    }
    get kindLabel() {
        return { field: _t("Field label"), menu: _t("Menu"), view: _t("Screen text (button, tab, title)") }[
            this.props.target.kind
        ];
    }
    get companyAllowed() {
        return ["field", "menu"].includes(this.props.target.kind);
    }
    async _send(extra) {
        this.state.busy = true;
        try {
            await this.orm.call("ncollection.label.override", "nc_quick_edit", [
                { ...this.props.target, scope: this.state.scope, ...extra },
            ]);
            browser.location.reload();
        } catch (error) {
            this.state.busy = false;
            throw error;
        }
    }
    save() {
        const value = (this.state.value || "").trim();
        if (!value) {
            this.notification.add(_t("The text cannot be empty."), { type: "warning" });
            return;
        }
        return this._send({ value });
    }
    reset() {
        return this._send({ reset: true });
    }
}

export class NcLabelEditorSystray extends Component {
    static template = "ncollection_core.NcLabelEditorSystray";
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.state = useState({ allowed: false, on: browser.sessionStorage.getItem(BODY_CLASS) === "1" });
        this.onClick = this.onClick.bind(this);
        onWillStart(async () => {
            this.state.allowed = await this.orm.call("ncollection.label.override", "nc_can_edit", []);
            if (this.state.allowed && this.state.on) {
                this._enable();
            }
        });
    }
    toggle() {
        this.state.on = !this.state.on;
        browser.sessionStorage.setItem(BODY_CLASS, this.state.on ? "1" : "0");
        this.state.on ? this._enable() : this._disable();
    }
    _enable() {
        document.body.classList.add(BODY_CLASS);
        document.addEventListener("click", this.onClick, true);
    }
    _disable() {
        document.body.classList.remove(BODY_CLASS);
        document.removeEventListener("click", this.onClick, true);
    }
    _model() {
        const controller = this.action.currentController;
        return (controller && controller.props && controller.props.resModel) || "";
    }
    /** The supported element under the click, as metadata, or null (then the click behaves normally). */
    _identify(el) {
        if (el.closest(".o_nc_label_editor_toggle, .modal")) {
            return null;
        }
        const menu = el.closest("[data-section]");
        if (menu) {
            return { kind: "menu", menu_id: parseInt(menu.dataset.section), text: menu.innerText.trim() };
        }
        const header = el.closest("th[data-name]");
        if (header) {
            return { kind: "field", model: this._model(), field: header.dataset.name, text: header.innerText.trim() };
        }
        const label = el.closest(".o_form_label, .o_td_label label, label.o_form_label");
        if (label) {
            const cell = label.closest(".o_cell, .o_wrap_label, .o_td_label") || label.parentElement;
            const widget = cell && (cell.nextElementSibling || cell).querySelector("[name]");
            const name = label.getAttribute("for") ? document.getElementById(label.getAttribute("for")) : null;
            const field = (widget && widget.getAttribute("name")) || (name && name.closest("[name]")?.getAttribute("name"));
            if (field) {
                return { kind: "field", model: this._model(), field, text: label.innerText.trim() };
            }
        }
        const button = el.closest("button[name], .nav-link, .o_form_label, .o_horizontal_separator");
        if (button && button.innerText.trim()) {
            return { kind: "view", model: this._model(), text: button.innerText.trim() };
        }
        return null;
    }
    onClick(ev) {
        const target = this._identify(ev.target);
        if (!target || !target.text) {
            return;
        }
        ev.preventDefault();
        ev.stopPropagation();
        this.dialog.add(NcLabelDialog, { target });
    }
}

registry.category("systray").add("ncollection_core.label_editor", { Component: NcLabelEditorSystray }, {
    sequence: 5,
});
