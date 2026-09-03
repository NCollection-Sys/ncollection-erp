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
        this.company = useService("company");
        this.state = useState({ apps: [], loaded: false });

        onWillStart(async () => {
            this.state.apps = await this.orm.call("ir.ui.menu", "nc_tenant_apps", []);
            this.state.loaded = true;
        });
    }

    get workspaceName() {
        return this.company.currentCompany.name || "";
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

    openApp(app) {
        if (app.action_id) {
            // doAction on the menu's own action keeps breadcrumbs and the
            // menu highlight identical to clicking it in the sidebar.
            this.action.doAction(app.action_id, { clearBreadcrumbs: true });
        }
    }
}

registry.category("actions").add("ncollection_core.tenant_home", NcTenantHome);
