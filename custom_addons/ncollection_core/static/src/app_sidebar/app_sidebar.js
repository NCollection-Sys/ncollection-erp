/** @odoo-module **/
// Persistent application sidebar (desktop / tablet). The apps are Odoo's own menu service apps, which the server
// already filtered for this user (ir.ui.menu groups): nothing here decides visibility. Navigation goes through
// menuService.selectMenu (client-side, no page reload). The expanded / collapsed choice is a per-user, per-database
// browser preference (no server state, never shared between users).
import { Component, useState, onWillDestroy } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { session } from "@web/session";
import { user } from "@web/core/user";
import { browser } from "@web/core/browser/browser";
import { WebClient } from "@web/webclient/webclient";
import { _t } from "@web/core/l10n/translation";

const DEFAULT_ICON = "/web/static/img/default_icon_app.png";

export function sidebarKey() {
    return `nc_app_sidebar:${session.db || ""}:${user.userId}`;
}

export const OVERLAY_WIDTH = 1100;

function isOverlay() {
    return browser.innerWidth <= OVERLAY_WIDTH;
}

// the saved choice wins; with none saved, tablets start collapsed so the content keeps its room
export function readCollapsed() {
    let saved = null;
    try {
        saved = browser.localStorage.getItem(sidebarKey());
    } catch {
        saved = null;
    }
    return saved ? saved === "collapsed" : isOverlay();
}

export function appIcon(app) {
    if (app.webIconData) {
        return { src: app.webIconData };
    }
    const [iconClass, color, backgroundColor] = (app.webIcon || "").split(",");
    if (iconClass && backgroundColor !== undefined) {
        return { glyph: { iconClass, style: `color:${color};background-color:${backgroundColor}` } };
    }
    return { src: DEFAULT_ICON };
}

export class NcAppSidebar extends Component {
    static template = "ncollection_core.AppSidebar";
    static props = {};

    setup() {
        this.menu = useService("menu");
        this.state = useState({ collapsed: readCollapsed(), current: this.menu.getCurrentApp()?.id || false });
        const onApp = () => {
            this.state.current = this.menu.getCurrentApp()?.id || false;
        };
        this.env.bus.addEventListener("MENUS:APP-CHANGED", onApp);
        onWillDestroy(() => {
            this.env.bus.removeEventListener("MENUS:APP-CHANGED", onApp);
            document.body.classList.remove("nc-sb-on", "nc-sb-collapsed");
        });
        this.applyBodyClass();
    }

    get apps() {
        return this.menu.getApps().map((app) => ({ id: app.id, name: app.name, xmlid: app.xmlid, app, ...appIcon(app) }));
    }

    href(app) {
        return `/odoo/${app.actionPath || "action-" + app.actionID}`;
    }

    applyBodyClass() {
        const cls = document.body.classList;
        cls.add("nc-sb-on");
        cls.toggle("nc-sb-collapsed", this.state.collapsed);
    }

    get toggleLabel() {
        return this.state.collapsed ? _t("Expand menu") : _t("Collapse menu");
    }

    toggle() {
        this.state.collapsed = !this.state.collapsed;
        try {
            browser.localStorage.setItem(sidebarKey(), this.state.collapsed ? "collapsed" : "expanded");
        } catch {
            // private window / blocked storage: the choice lasts for this page only
        }
        this.applyBodyClass();
    }

    open(ev, item) {
        if (ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.button === 1) {
            return; // the browser opens the link in a new tab
        }
        ev.preventDefault();
        this.menu.selectMenu(item.app);
        if (isOverlay() && !this.state.collapsed) {
            // on a tablet the expanded bar overlays the page: close it once an application is chosen
            this.state.collapsed = true;
            this.applyBodyClass();
        }
    }
}

WebClient.components = { ...WebClient.components, NcAppSidebar };
patch(WebClient.prototype, {
    setup() {
        super.setup(...arguments);
        this.ncUi = useState(useService("ui"));
    },
});
