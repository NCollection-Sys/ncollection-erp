/** @odoo-module **/
// Persistent application sidebar (desktop / tablet). The apps are Odoo's own menu service apps, which the server
// already filtered for this user (ir.ui.menu groups): nothing here decides visibility. Navigation goes through
// menuService.selectMenu (client-side, no page reload). The expanded / collapsed choice is a per-user, per-database
// browser preference (no server state, never shared between users). The page margin follows the bar through CSS
// :has(), so no class is left behind when the bar is not rendered (phones, fullscreen).
import { Component, onWillDestroy, useExternalListener, useRef, useState } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { session } from "@web/session";
import { user } from "@web/core/user";
import { browser } from "@web/core/browser/browser";
import { localization } from "@web/core/l10n/localization";
import { WebClient } from "@web/webclient/webclient";
import { _t } from "@web/core/l10n/translation";


export const DEFAULT_ICON = "/web/static/img/default_icon_app.png";

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
        this.root = useRef("root");
        this.toggleRef = useRef("toggle");
        this.state = useState({ collapsed: readCollapsed(), current: this.menu.getCurrentApp()?.id || false });
        const onApp = () => {
            this.state.current = this.menu.getCurrentApp()?.id || false;
        };
        this.env.bus.addEventListener("MENUS:APP-CHANGED", onApp);
        onWillDestroy(() => this.env.bus.removeEventListener("MENUS:APP-CHANGED", onApp));
        // a tablet overlay closes on Escape and on a click outside it
        useExternalListener(window, "keydown", (ev) => {
            if (ev.key === "Escape" && this.overlaid) {
                this.close();
                this.toggleRef.el?.focus();
            }
        });
        // the tap that closes the overlay only closes it: it never also activates what lay under the shadow
        useExternalListener(window, "pointerdown", (ev) => {
            this.swallowClick = false;               // a press that produced no click never eats a later one
            if (this.overlaid && this.root.el && !this.root.el.contains(ev.target)) {
                ev.preventDefault();
                ev.stopPropagation();
                this.swallowClick = ev.button === 0;
                this.close();
            }
        }, { capture: true });
        useExternalListener(window, "click", (ev) => {
            if (this.swallowClick) {
                this.swallowClick = false;
                ev.preventDefault();
                ev.stopPropagation();
            }
        }, { capture: true });
        for (const type of ["pointercancel", "contextmenu"]) {
            useExternalListener(window, type, () => {
                this.swallowClick = false;
            }, { capture: true });
        }
        // keyboard: focus leaving the open overlay (Tab) closes it, so nothing covered can be operated unseen
        useExternalListener(window, "focusin", (ev) => {
            if (this.overlaid && this.root.el && !this.root.el.contains(ev.target)) {
                this.close();
            }
        });
    }

    get overlaid() {
        return !this.state.collapsed && isOverlay();
    }

    get rtl() {
        return localization.direction === "rtl";
    }

    // the chevron points where the bar will move (Odoo mirrors directional icons in RTL itself)
    get chevron() {
        return this.state.collapsed ? "fa-angle-double-right" : "fa-angle-double-left";
    }

    get tooltipPosition() {
        return this.rtl ? "left" : "right";
    }

    get apps() {
        return this.menu.getApps().map((app) => ({ id: app.id, name: app.name, xmlid: app.xmlid, app, ...appIcon(app) }));
    }

    href(app) {
        return `/odoo/${app.actionPath || "action-" + app.actionID}`;
    }

    get toggleLabel() {
        return this.state.collapsed ? _t("Expand menu") : _t("Collapse menu");
    }

    close() {
        this.state.collapsed = true; // not saved: closing a tablet overlay is not a preference
    }

    toggle() {
        this.state.collapsed = !this.state.collapsed;
        try {
            browser.localStorage.setItem(sidebarKey(), this.state.collapsed ? "collapsed" : "expanded");
        } catch {
            // private window / blocked storage: the choice lasts for this page only
        }
    }

    open(ev, item) {
        if (ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.button === 1) {
            return; // the browser opens the link in a new tab
        }
        ev.preventDefault();
        this.menu.selectMenu(item.app);
        if (this.overlaid) {
            this.close();
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
