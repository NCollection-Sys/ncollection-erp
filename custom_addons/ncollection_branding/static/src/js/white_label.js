/** @odoo-module **/
/*
 * White-label JS surfaces (P1-T13).
 *
 * Three Odoo-string surfaces live in JS, not in server templates, and can
 * only be rebranded here (recon-verified against Odoo 19 source):
 *
 * 1. Tab-title fallback: title_service.js joins title parts with
 *    `|| "Odoo"` — a raw JS literal (no _t, no template) that OVERWRITES
 *    the server-rendered <title> at runtime whenever no action is loaded
 *    (e.g. right after login). We re-register the "title" service with the
 *    brand as the empty-fallback, mirroring core otherwise so per-action
 *    titles keep Odoo's action-name-only behaviour. Mirrors
 *    web/core/browser/title_service.js — re-check on Odoo upgrade (a test
 *    asserts document.title never becomes "Odoo").
 *
 * 2. User menu "My Odoo.com Account": a user_menuitems registry entry —
 *    removed outright (a tenant has no odoo.com account to manage).
 *
 * 3. Error dialog titles ("Odoo Error", "Odoo Server Error", ...): static
 *    class properties on the exported dialog components. Reassigned here.
 *    Deliberately NOT done via translation overrides: _t() returns the
 *    source string unchanged when the active language is the source
 *    language, so an en_US .po would silently not apply (classic trap).
 *
 * All patches are additive against exported/registry surfaces — no core
 * file edits, upgrade-checked by tests/test_webclient_branding.py.
 */

import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import {
    ErrorDialog,
    ClientErrorDialog,
    NetworkErrorDialog,
    RPCErrorDialog,
} from "@web/core/errors/error_dialogs";

const BRAND = "NCollection ERP";

/* 1 — tab title: re-register the service with the brand as the empty
 * fallback (core hardcodes "Odoo"). Identical to core otherwise. */
const brandedTitleService = {
    start() {
        const titleCounters = {};
        const titleParts = {};
        function updateTitle() {
            const counter = Object.values(titleCounters).reduce((acc, c) => acc + c, 0);
            const name = Object.values(titleParts).join(" - ") || BRAND;
            document.title = counter ? `(${counter}) ${name}` : name;
        }
        return {
            get current() {
                return document.title;
            },
            getParts() {
                return Object.assign({}, titleParts);
            },
            setCounters(counters) {
                for (const key in counters) {
                    const val = counters[key];
                    if (!val) {
                        delete titleCounters[key];
                    } else {
                        titleCounters[key] = val;
                    }
                }
                updateTitle();
            },
            setParts(parts) {
                for (const key in parts) {
                    const val = parts[key];
                    if (!val) {
                        delete titleParts[key];
                    } else {
                        titleParts[key] = val;
                    }
                }
                updateTitle();
            },
        };
    },
};
registry.category("services").add("title", brandedTitleService, { force: true });

/* 2 — drop the "My Odoo.com Account" user-menu entry. */
const userMenuRegistry = registry.category("user_menuitems");
if (userMenuRegistry.contains("odoo_account")) {
    userMenuRegistry.remove("odoo_account");
}

/* 3 — rebrand error dialog titles. */
ErrorDialog.title = _t("%s Error", BRAND);
ClientErrorDialog.title = _t("%s Client Error", BRAND);
NetworkErrorDialog.title = _t("%s Network Error", BRAND);
RPCErrorDialog.title = _t("%s Server Error", BRAND);

/* 4 — remove the Odoo "Onboarding" tour trigger (#472).
 *
 * `web_tour`'s tour service registers an OnboardingItem into
 * registry.category("debug").category("default") from inside its own `start()`,
 * which runs AFTER every module's top-level code. A plain remove() here would
 * therefore run first and find nothing, and the entry would appear anyway —
 * which is why this listens instead of removing once.
 *
 * Registries emit "UPDATE" on every add, so this drops the entry the moment it
 * is added and stays correct however the tour service is loaded or reloaded. It
 * touches ONLY that one key: every other debug entry, and the tour service
 * itself, are left alone (the server-side `tour_enabled` override in
 * models/res_users.py is what actually stops tours running — this removes the
 * Odoo-branded control, not the machinery the test tours need).
 *
 * No CSS, no core file edit, no monkey-patch of an unexported symbol.
 */
const debugDefaultRegistry = registry.category("debug").category("default");
const ONBOARDING_ITEM = "onboardingItem";

function dropOnboardingItem() {
    if (debugDefaultRegistry.contains(ONBOARDING_ITEM)) {
        debugDefaultRegistry.remove(ONBOARDING_ITEM);
    }
}
debugDefaultRegistry.addEventListener("UPDATE", dropOnboardingItem);
dropOnboardingItem();
