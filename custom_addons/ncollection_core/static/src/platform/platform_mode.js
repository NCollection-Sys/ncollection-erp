/** @odoo-module **/
import { Component, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { session } from "@web/session";

/**
 * A strip across every screen while a Platform Administrator is inside a tenant:
 * which company is active, who is administering it, and the way back to the platform.
 * Rendered only when the SERVER says the session is a platform proxy (session_info.nc_platform_mode); it is
 * a reminder, never a control the server relies on.
 */
export class NcPlatformModeBar extends Component {
    static template = "ncollection_core.PlatformModeBar";
    static props = ["*"];

    setup() {
        this.mode = session.nc_platform_mode || null;
        // the administration logo of the PRODUCT (Product Branding), not the company's
        this.adminLogo = session.nc_product_brand?.admin_logo_url || "";
        onMounted(() => document.body.classList.add("nc-platform-mode"));
        onWillUnmount(() => document.body.classList.remove("nc-platform-mode"));
    }
}

if (session.nc_platform_mode) {
    registry.category("main_components").add("ncollection_core.PlatformModeBar", {
        Component: NcPlatformModeBar,
    });
}
