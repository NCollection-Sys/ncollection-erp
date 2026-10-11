/** @odoo-module **/
/**
 * Self-healing "unread badge" IndexedDB store (the `InvalidStateError ... The database connection is
 * closing` client error).
 *
 * ROOT CAUSE. Odoo's mail module (`@mail/core/web/store_service_patch`) builds ONE idb-keyval `Store`
 * at module load: idb-keyval 3.2.0 opens a single IDBDatabase connection in its constructor and keeps
 * it for the life of the page (it never reopens). `Store.updateAppBadge()` then does
 * `idbKeyval.set("unread", n, store)` and drops the returned promise. When the browser closes that
 * connection under the page (storage cleared, origin data evicted, a tab frozen/restored, another
 * context deleting the database) the next badge update runs `db.transaction()` on a closing connection,
 * rejects, and nobody handles the rejection: Odoo shows "UncaughtPromiseError > InvalidStateError".
 * That is the `idb-keyval _withIDBStore -> db.transaction` frame seen in `web.assets_web.min.js`.
 *
 * FIX (narrow, no core change). Override only `updateAppBadge`: keep the same store and the same write,
 * but if the connection is closing drop that dead store and reopen it ONCE. Any other error is not
 * swallowed (it stays an unhandled rejection, exactly as in core), and a second failure is not retried,
 * so there is no reconnect loop and no leaked connection.
 */
import { Store } from "@mail/core/common/store_service";
import { patch } from "@web/core/utils/patch";

const DB_NAME = "odoo-mail-unread-db";
const STORE_NAME = "odoo-mail-unread-store";

let unreadStore;

function getUnreadStore() {
    if (!window.idbKeyval) {
        return undefined;
    }
    if (!unreadStore) {
        unreadStore = new window.idbKeyval.Store(DB_NAME, STORE_NAME);
    }
    return unreadStore;
}

/** Drops the cached connection so the next write opens a new one. */
export function resetUnreadStore() {
    unreadStore = undefined;
}

export async function writeUnread(counter) {
    const store = getUnreadStore();
    if (!store) {
        return;
    }
    try {
        await window.idbKeyval.set("unread", counter, store);
    } catch (error) {
        if (error?.name !== "InvalidStateError") {
            throw error;
        }
        // The browser closed the long-lived connection: reopen once and write again.
        resetUnreadStore();
        await window.idbKeyval.set("unread", counter, getUnreadStore());
    }
}

patch(Store.prototype, {
    updateAppBadge() {
        if (window.idbKeyval) {
            writeUnread(this.globalCounter);
            Promise.resolve(navigator.setAppBadge?.(this.globalCounter)).catch(() => {}); // as in core
        }
    },
});
