/** @odoo-module **/
// UI-01: a Collapse / Expand control on Odoo's OWN chatter panel (Send message, Log note, Activity, messages,
// attachments, followers). Nothing about the chatter is re-implemented: the native component is patched.
//
// Collapsing never unmounts the component: it only hides it with a CSS class, so an unsent message or note, the
// composer's attachments, the scroll position and every open box survive, and reopening is instant. The preference is
// the user's own: it is kept in this browser under a key that names the user, so it never affects anybody else.
import { Chatter } from "@mail/chatter/web_portal/chatter";
import { browser } from "@web/core/browser/browser";
import { user } from "@web/core/user";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { onMounted, onPatched, useEffect, useState } from "@odoo/owl";

const COLLAPSED_CLASS = "o_nc_chatter_collapsed";

function storageKey() {
    return `nc.chatter.collapsed.${user.userId}`;
}

function readPreference() {
    try {
        return browser.localStorage.getItem(storageKey()) === "1";
    } catch {
        return false; // storage blocked: the panel simply starts expanded
    }
}

function writePreference(collapsed) {
    try {
        if (collapsed) {
            browser.localStorage.setItem(storageKey(), "1");
        } else {
            browser.localStorage.removeItem(storageKey());
        }
    } catch {
        // not persisted; the toggle still works for this page
    }
}

patch(Chatter.prototype, {
    setup() {
        super.setup(...arguments);
        this.ncChatter = useState({ collapsed: readPreference() });
        // The class is re-asserted after every mount and patch: the root element can be (re)created by Odoo's own
        // rendering (a new record, a layout change), and a preference read at start must apply on the very first paint.
        const sync = () => {
            const el = this.rootRef.el;
            if (el) {
                el.classList.toggle(COLLAPSED_CLASS, this.ncChatter.collapsed);
            }
        };
        onMounted(sync);
        onPatched(sync);
        useEffect(sync, () => [this.ncChatter.collapsed, this.state.thread]);
    },

    get ncShowCollapseToggle() {
        return !this.env.inDialog && !this.props.compactHeight;
    },

    get ncToggleLabel() {
        return this.ncChatter.collapsed
            ? _t("Show the history panel")
            : _t("Collapse the history panel");
    },

    ncToggleCollapse() {
        this.ncChatter.collapsed = !this.ncChatter.collapsed;
        writePreference(this.ncChatter.collapsed);
    },
});
