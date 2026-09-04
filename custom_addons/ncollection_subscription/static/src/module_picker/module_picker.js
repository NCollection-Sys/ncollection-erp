/** @odoo-module **/
/*
 * Plan module picker (#457, redesigned in #467).
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
 *
 * #467 adds bulk actions, category/tab filtering and a full-width responsive
 * grid, because the catalog stopped being short the moment the native
 * accounting modules became selectable. Three properties are load-bearing and
 * each has a test:
 *
 *  - CORE MODULES ARE NOT TOGGLEABLE, by any path. Provisioning installs them
 *    whatever the plan says, so no bulk action may add or remove one.
 *  - A NAME THE CATALOG DOES NOT KNOW IS NEVER DROPPED SILENTLY. The stored
 *    string can legitimately contain modules that are not `application=True`
 *    (`ncollection_mis_templates` is exactly that, and it is licensed on
 *    ENTERPRISE today). "Clear selection" clearing those would revoke a live
 *    tenant's modules through a button labelled as a UI convenience. They are
 *    rendered as their own "not in catalog" cards instead — visible, and
 *    removable one at a time.
 *  - DEPENDENCIES ARE SHOWN, NOT RESOLVED. A module implied by something else
 *    the plan names is displayed as included, so an operator can see why. The
 *    authoritative expansion stays server-side (Ring 1's
 *    `_ncollection_expand_dependencies`), and provisioning installs the closure
 *    regardless — duplicating it here would create a second answer to the same
 *    question.
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

/**
 * Transitive closure of `names` over `dependsByName`, for DISPLAY only.
 *
 * Exported so the test can pin the cycle behaviour: a module list is a DAG in
 * Odoo, but a corrupt registry must not hang the browser, so the walk is
 * bounded by the visited set rather than trusting acyclicity.
 */
export function expandDependencies(names, dependsByName) {
    const out = new Set(names);
    const frontier = [...names];
    while (frontier.length) {
        const current = frontier.pop();
        for (const dep of dependsByName[current] || []) {
            if (!out.has(dep)) {
                out.add(dep);
                frontier.push(dep);
            }
        }
    }
    return out;
}

export class NcModulePicker extends Component {
    static template = "ncollection_subscription.ModulePicker";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.state = useState({
            core: [],
            optional: [],
            loaded: false,
            query: "",
            category: "",
            // "all" | "included" — a view filter, never a different data set.
            tab: "all",
        });

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

    get optionalByName() {
        return Object.fromEntries(this.state.optional.map((m) => [m.name, m]));
    }

    /** Names the plan stores that the catalog does not offer. Rendered rather
     *  than ignored — see the "never dropped silently" note above. */
    get unlisted() {
        const known = new Set([
            ...this.state.optional.map((m) => m.name),
            ...this.state.core.map((m) => m.name),
        ]);
        return this.selected
            .filter((name) => !known.has(name))
            .map((name) => ({ name, label: name, summary: "", icon: "", unlisted: true }));
    }

    /** Everything the plan effectively licenses, literal names plus whatever
     *  they depend on. Display only. */
    get effective() {
        const dependsByName = {};
        for (const module of [...this.state.optional, ...this.state.core]) {
            dependsByName[module.name] = module.depends || [];
        }
        return expandDependencies(this.selected, dependsByName);
    }

    isSelected(module) {
        return this.selected.includes(module.name);
    }

    /** Licensed because something else selected depends on it, not because it
     *  was picked. The card says so instead of looking unselected. */
    isImplied(module) {
        return !this.isSelected(module) && this.effective.has(module.name);
    }

    get categories() {
        const seen = new Set(
            this.state.optional.map((m) => m.category).filter(Boolean)
        );
        return [...seen].sort((a, b) => a.localeCompare(b));
    }

    /**
     * The current search + category + tab result. "Select visible results"
     * operates on exactly this list, so what the button does is what the
     * operator can see.
     */
    get visibleOptional() {
        const query = this.state.query.trim().toLowerCase();
        return this.state.optional.filter((m) => {
            if (this.state.category && m.category !== this.state.category) {
                return false;
            }
            if (this.state.tab === "included" && !this.isSelected(m) && !this.isImplied(m)) {
                return false;
            }
            if (!query) {
                return true;
            }
            return (
                m.label.toLowerCase().includes(query) ||
                m.name.toLowerCase().includes(query) ||
                (m.summary || "").toLowerCase().includes(query)
            );
        });
    }

    /** Core cards join the same grid rather than owning a second one — with a
     *  long catalog, a separate always-included block was mostly whitespace.
     *  They are filtered by search but never by the "included" tab, because
     *  they are always included. */
    get visibleCore() {
        const query = this.state.query.trim().toLowerCase();
        if (this.state.category) {
            return [];
        }
        if (!query) {
            return this.state.core;
        }
        return this.state.core.filter(
            (m) =>
                m.label.toLowerCase().includes(query) ||
                m.name.toLowerCase().includes(query)
        );
    }

    iconSrc(module) {
        return module.icon ? `data:image/png;base64,${module.icon}` : "";
    }

    initials(module) {
        return (module.label || module.name || "?").trim().charAt(0).toUpperCase();
    }

    /**
     * The ONE place the field is written. Every toggle and bulk action funnels
     * through it, so the invariants hold once instead of per caller:
     * the value is sorted (an unstable order would make every open-and-save
     * look like a plan change and push a pointless config sync to every
     * tenant), and core names can never reach it.
     */
    _write(names) {
        if (this.props.readonly) {
            return;
        }
        const core = new Set(this.state.core.map((m) => m.name));
        const clean = [...new Set(names)].filter((name) => !core.has(name));
        this.props.record.update({ [this.props.name]: clean.sort().join(",") });
    }

    toggle(module) {
        if (this.props.readonly || module.core) {
            return;
        }
        const current = this.selected;
        this._write(
            current.includes(module.name)
                ? current.filter((n) => n !== module.name)
                : [...current, module.name]
        );
    }

    selectAll() {
        this._write([...this.selected, ...this.state.optional.map((m) => m.name)]);
    }

    selectVisible() {
        this._write([...this.selected, ...this.visibleOptional.map((m) => m.name)]);
    }

    /** Clears the CATALOG selection. Names the catalog does not know are kept:
     *  they are licensed on live tenants and this button is a convenience, not
     *  a revocation tool. They stay individually removable. */
    clearSelection() {
        const offered = new Set(this.state.optional.map((m) => m.name));
        this._write(this.selected.filter((name) => !offered.has(name)));
    }

    resetFilters() {
        this.state.query = "";
        this.state.category = "";
        this.state.tab = "all";
    }
}

export const ncModulePicker = {
    component: NcModulePicker,
    displayName: "NCollection Module Picker",
    supportedTypes: ["char", "text"],
};

registry.category("fields").add("nc_module_picker", ncModulePicker);
