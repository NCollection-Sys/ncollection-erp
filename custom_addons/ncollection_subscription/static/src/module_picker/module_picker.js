/** @odoo-module **/
/*
 * Plan module picker (#457).
 *
 * A field widget for `ncollection.subscription.plan.allowed_module_names`. It
 * replaces typing `crm,stock` by hand — nothing else. The FIELD IS UNCHANGED:
 * this reads and writes the same comma-separated technical-name string that
 * provisioning (`_module_list()`) and config sync already consume, so the
 * licensing contract has exactly one representation and the picker is an input
 * method rather than a second model.
 *
 * The catalog is real: `get_selectable_modules()` reads `ir.module.module`, the
 * platform's actual addons path, so the picker cannot offer a module that does
 * not exist. Icons are each module's own `icon_image`. Nothing is hardcoded
 * here and no demo records are created — `ncollection.module` stays dead code.
 */
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { useService } from "@web/core/utils/hooks";

/** "crm, sale,crm" -> ["crm", "sale"] — the plan's own parser, in JS. */
export function parseModuleNames(value) {
    const seen = new Set();
    for (const raw of (value || "").split(",")) {
        const name = raw.trim();
        if (name) {
            seen.add(name);
        }
    }
    return [...seen];
}

export class NcModulePicker extends Component {
    static template = "ncollection_subscription.ModulePicker";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ core: [], optional: [], loaded: false, query: "" });

        onWillStart(async () => {
            const catalog = await this.orm.call(
                "ncollection.subscription.plan", "get_selectable_modules", []
            );
            this.state.core = catalog.core || [];
            this.state.optional = catalog.optional || [];
            this.state.loaded = true;
        });
    }

    /** Selected technical names, read from the field itself — the field stays
     *  the single source of truth, so an edit made anywhere else (import, a
     *  server action) is reflected here without a sync step. */
    get selected() {
        return parseModuleNames(this.props.record.data[this.props.name]);
    }

    get visibleOptional() {
        const query = this.state.query.trim().toLowerCase();
        if (!query) {
            return this.state.optional;
        }
        return this.state.optional.filter(
            (m) =>
                m.label.toLowerCase().includes(query) ||
                m.name.toLowerCase().includes(query) ||
                (m.summary || "").toLowerCase().includes(query)
        );
    }

    isSelected(module) {
        return this.selected.includes(module.name);
    }

    iconSrc(module) {
        return module.icon ? `data:image/png;base64,${module.icon}` : "";
    }

    initials(module) {
        return (module.label || module.name || "?").trim().charAt(0).toUpperCase();
    }

    toggle(module) {
        if (this.props.readonly) {
            return;
        }
        const current = this.selected;
        const next = current.includes(module.name)
            ? current.filter((n) => n !== module.name)
            : [...current, module.name];
        // Written back in the field's own format. Sorted so the stored string is
        // stable — an unstable order would make every open-and-save look like a
        // plan change and push a pointless config sync to every tenant.
        this.props.record.update({ [this.props.name]: next.sort().join(",") });
    }
}

export const ncModulePicker = {
    component: NcModulePicker,
    displayName: "NCollection Module Picker",
    supportedTypes: ["char", "text"],
};

registry.category("fields").add("nc_module_picker", ncModulePicker);
