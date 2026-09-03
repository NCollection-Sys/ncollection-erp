/** @odoo-module **/
/*
 * Tenant application launcher (#455).
 *
 * The card grid a tenant user lands on. It renders EXACTLY what
 * `ir.ui.menu.nc_tenant_apps()` returns and decides nothing about
 * eligibility itself — that answer comes from Odoo's own
 * `get_user_roots()`, which has already applied the user's group
 * permissions, Ring 1 plan licensing (`allowed_module_names` + its
 * dependency closure) and the owner-only Apps/Settings subtraction.
 *
 * So there is no module list in this file, and there must never be one: a
 * client-side list would be a second source of truth that drifts from the
 * plan, which is the whole failure mode #455 exists to remove. Icons are the
 * modules' own `web_icon_data` as Odoo already stores them — nothing invented.
 */
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";

export class NcTenantHome extends Component {
    static template = "ncollection_core.TenantHome";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        // #459: navigation goes through the MENU service, not a bare doAction,
        // so a card behaves exactly like clicking the app in the sidebar —
        // including setting the current-app highlight. selectMenu() is Odoo's
        // own entry point; nothing here builds a URL.
        this.menu = useService("menu");
        // #457: this used to request a "company" service. There is NO such
        // service in Odoo 19, so it threw "Service company is not available"
        // from setup() — before the first render — and every tenant home load
        // failed. The current company lives on `user.activeCompany`, which is
        // how switch_company_item.js, pivot_renderer.js and graph_model.js all
        // read it in the shipped code. Deliberately NOT wrapped in try/catch:
        // swallowing a missing dependency would hide the next one exactly the
        // same way, and a blank home page is harder to diagnose than a crash.
        // test_tenant_home.py pins both halves of that.
        this.state = useState({ apps: [], loaded: false });

        onWillStart(async () => {
            this.state.apps = await this.orm.call("ir.ui.menu", "nc_tenant_apps", []);
            this.state.loaded = true;
        });
    }

    get workspaceName() {
        return user.activeCompany?.name || "";
    }

    get userName() {
        return user.name || "";
    }

    iconSrc(app) {
        // The module's own icon, base64 from web_icon_data. When a menu has
        // none, the template falls back to a lettermark rather than shipping a
        // substitute icon for a real module.
        return app.web_icon_data ? `data:image/png;base64,${app.web_icon_data}` : "";
    }

    initials(app) {
        return (app.name || "?").trim().charAt(0).toUpperCase();
    }

    async openApp(app) {
        // `menu_id` is the menu that OWNS the action — the app root itself when
        // it has one, otherwise its first actionable child (CRM, Calendar and
        // Contacts are all containers). The server resolved that; every app in
        // the payload is guaranteed to have one, because one without a
        // reachable action is not returned at all (#459).
        await this.menu.selectMenu(app.menu_id);
    }
}

registry.category("actions").add("ncollection_core.tenant_home", NcTenantHome);
