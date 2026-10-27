/** @odoo-module **/
/**
 * The client half of the public application path (ncollection_core/app_url.py): the address bar shows /atlas/...,
 * never /odoo/... . /web/... (Odoo's technical endpoints) is not touched.
 *
 * Built ONLY on what Odoo's router exports for this purpose (web/core/browser/router.js says: "state <-> url
 * conversions can be patched if needed in a custom webclient"):
 *   - router.stateToUrl: Odoo builds "/odoo/<path>?<query>"; the same URL is answered under the application path;
 *   - router.urlToState: a URL under the application path is read exactly as Odoo reads its "/odoo" equivalent
 *     (actions, records, active ids, query) - Odoo's own parser does all the work;
 *   - startRouter(): Odoo computes the initial state while router.js loads, before this patch exists; re-running it
 *     (exported, idempotent: it only sets the router's variables) reads the current URL with the patched parser.
 * Every internal conversion in router.js goes through router.stateToUrl / router.urlToState (push, replace, popstate,
 * clicks), so history, reload, deep links and back / forward follow.
 *
 * The one thing Odoo keeps private is its click handler for internal links, which only acts while the page itself is
 * under "/odoo". The listener below does the same for a page under the application path: an internal link (/web,
 * /odoo/..., /atlas/...) becomes a state change instead of a full reload, through Odoo's own popstate handler (which
 * adopts ev.state.nextState and triggers ROUTE_CHANGE, as Odoo's click handler does). Links Odoo itself renders still
 * say /odoo/... (e-mails, chatter, startUrl() users); they work: clicked here they are routed in place, opened
 * elsewhere the server redirects them to the application path.
 *
 * Regression guard: ncollection_core/tests/test_app_url.py fails if an Odoo update moves any of these seams.
 */
import { browser } from "@web/core/browser/browser";
import { router, startRouter } from "@web/core/browser/router";
import { patch } from "@web/core/utils/patch";
import { session } from "@web/session";
import { WebClient } from "@web/webclient/webclient";

const ODOO_PATH = "/odoo";
const VALID = /^\/[a-z][a-z0-9_-]{1,30}$/;
export const APP_PATH = VALID.test(session.nc_app_path || "") ? session.nc_app_path : "/atlas";

function under(pathname, prefix) {
    return pathname === prefix || pathname.startsWith(prefix + "/");
}

/** "/odoo/x?y" -> "/atlas/x?y" (any other path unchanged). */
export function toAppPath(path) {
    const [pathname] = path.split(/[?#]/);
    return under(pathname, ODOO_PATH) ? APP_PATH + path.slice(ODOO_PATH.length) : path;
}

const odooStateToUrl = router.stateToUrl;
const odooUrlToState = router.urlToState;

router.stateToUrl = function (state) {
    return toAppPath(odooStateToUrl.call(this, state));
};

router.urlToState = function (urlObj) {
    if (under(urlObj.pathname, APP_PATH)) {
        const odooUrl = new URL(urlObj.href);
        odooUrl.pathname = ODOO_PATH + urlObj.pathname.slice(APP_PATH.length);
        return odooUrlToState.call(this, odooUrl);
    }
    return odooUrlToState.call(this, urlObj);
};

startRouter();

// The installable app's service worker: Odoo registers it for scope "/odoo" (WebClient.registerServiceWorker), which
// leaves every application page outside it. Same registration, under the application path (the server allows that
// scope: controllers/app_routes.py NcApplicationPathPwa).
patch(WebClient.prototype, {
    registerServiceWorker() {
        const container = navigator.serviceWorker;
        if (!container) {
            return super.registerServiceWorker(...arguments);
        }
        const register = container.register;
        container.register = (url, options = {}) =>
            register.call(container, url, options.scope === ODOO_PATH ? { ...options, scope: APP_PATH } : options);
        try {
            return super.registerServiceWorker(...arguments);
        } finally {
            delete container.register;
        }
    },
});

browser.addEventListener("click", (ev) => {
    if (ev.defaultPrevented || ev.target.closest?.("[contenteditable]")) {
        return;
    }
    const a = ev.target.closest?.("a");
    const href = a?.getAttribute("href");
    if (!href || href.startsWith("#") || a.target === "_blank") {
        return;
    }
    let url;
    try {
        url = new URL(a.href);
    } catch {
        return;
    }
    if (url.host !== browser.location.host || !under(browser.location.pathname, APP_PATH)) {
        return;
    }
    if (!(url.pathname === "/web" || under(url.pathname, ODOO_PATH) || under(url.pathname, APP_PATH))) {
        return;
    }
    ev.preventDefault();
    url.pathname = toAppPath(url.pathname);        // /web (legacy #hash links) is read by Odoo as it is
    const nextState = router.urlToState(url);
    browser.dispatchEvent(new PopStateEvent("popstate", { state: { nextState } }));
});
