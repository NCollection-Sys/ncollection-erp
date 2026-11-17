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
import { rpcBus } from "@web/core/network/rpc";
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
            // Odoo's web client shows menus and views from its own caches first (stale-while-revalidate): drop
            // them, so the reload renders the text the server now serves.
            rpcBus.trigger("CLEAR-CACHES");
            browser.localStorage.removeItem("webclient_menus");
            // The persistent cache is IndexedDB: drop it outright and only then reload.
            await new Promise((resolve) => {
                const request = browser.indexedDB ? browser.indexedDB.deleteDatabase("rpc") : null;
                if (!request) {
                    return resolve();
                }
                request.onsuccess = request.onerror = request.onblocked = () => resolve();
                browser.setTimeout(resolve, 1500);
            });
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
            const text = (header.querySelector(".o_column_title, span") || header).innerText.trim();
            return { kind: "field", model: this._model(), field: header.dataset.name, text };
        }
        const label = el.closest("label.o_form_label, .o_td_label label");
        if (label) {
            // The label's own words, without Odoo's "?" help marker (<sup>).
            const text = [...label.childNodes].filter((n) => n.nodeName !== "SUP").map((n) => n.textContent)
                .join("").trim();
            // The field is the widget the label points at (for="<field>_<n>"), never a neighbouring container.
            const input = label.getAttribute("for") && document.getElementById(label.getAttribute("for"));
            const row = label.closest(".o_cell, .o_wrap_label, .o_td_label");
            const widget = (input && input.closest(".o_field_widget[name]")) ||
                (row && row.nextElementSibling && row.nextElementSibling.querySelector(".o_field_widget[name]"));
            if (widget) {
                return { kind: "field", model: this._model(), field: widget.getAttribute("name"), text };
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
