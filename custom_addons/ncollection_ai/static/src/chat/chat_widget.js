/** @odoo-module **/
/*
 * P5-T06 (#63) — the floating AI assistant, client half.
 *
 * Everything here is PRESENTATION. Every decision that matters — who may use
 * the assistant, which prompts exist, whether free text is accepted — is made
 * server-side in ncollection.ai.chat, and this file treats the server's
 * answers as the only truth:
 *
 *   - `enabled` false  -> the component renders nothing. That is UX, not
 *     security: the server methods re-check the gate on every call.
 *   - suggestions are (key, label) pairs; the canonical question text never
 *     reaches the client until the server echoes the persisted message back.
 *   - the typed input exists in the DOM only when the server said free_text
 *     is on — and submitting it still lands in ask() with all its gates.
 *
 * MODEL OUTPUT IS UNTRUSTED DATA. The renderer below escapes EVERYTHING
 * first, then rebuilds a deliberately tiny markdown subset (bold, inline
 * code, "- " bullets, paragraphs) out of the escaped text. There is no path
 * from provider output to raw HTML, no t-raw, no innerHTML assignment.
 */
import { Component, markup, onWillStart, useEffect, useRef, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { router, routerBus } from "@web/core/browser/router";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { useBus, useService } from "@web/core/utils/hooks";

// Namespaced per user: two people sharing a browser profile must not inherit
// each other's open/closed panel state (the transcript itself is fetched
// per-session and server-gated — only this boolean was shared).
const OPEN_STORAGE_KEY = `nc_ai_chat_open.${user.userId}`;

function escapeHtml(text) {
    return String(text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#x27;");
}

/* Inline transforms run on ALREADY-ESCAPED text, so the only tags in the
 * output are the ones written here. Bold and inline code only — italics via
 * single `*` collide with arithmetic and bullet markers in real answers. */
function inlineMd(escaped) {
    return escaped
        .replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

export function renderMarkdownSubset(text) {
    const parts = [];
    let items = null;
    const flush = () => {
        if (items) {
            parts.push("<ul><li>" + items.join("</li><li>") + "</li></ul>");
            items = null;
        }
    };
    for (const rawLine of String(text || "").split(/\r?\n/)) {
        const escaped = escapeHtml(rawLine);
        const bullet = escaped.match(/^\s*-\s+(.*)$/);
        if (bullet) {
            items = items || [];
            items.push(inlineMd(bullet[1]));
            continue;
        }
        flush();
        if (escaped.trim()) {
            parts.push("<p>" + inlineMd(escaped) + "</p>");
        }
    }
    flush();
    // markup() is safe here BECAUSE of the escape-first construction above;
    // it must never be handed anything containing un-escaped input.
    return markup(parts.join(""));
}

export class NcAiChatWidget extends Component {
    static template = "ncollection_ai.ChatWidget";
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.messagesRef = useRef("messages");
        this.state = useState({
            enabled: false,
            open: browser.localStorage.getItem(OPEN_STORAGE_KEY) === "1",
            freeText: false,
            suggestions: [],
            messages: [],
            sending: false,
            unread: false,
            error: "",
            typed: "",
        });

        onWillStart(async () => {
            await this.refreshState();
        });

        // Suggestions follow the user around: when the route changes while
        // the panel is open, re-ask the server for the prompts that fit the
        // new module context. (Server falls back to 'default' for anything
        // it does not recognise.) Fire-and-forget ON PURPOSE, so it must
        // swallow its own rejection: a transient failure of a cosmetic
        // refresh must not raise Odoo's traceback dialog over nothing.
        useBus(routerBus, "ROUTE_CHANGE", () => {
            if (this.state.enabled && this.state.open) {
                this.refreshSuggestions().catch(() => {});
            }
        });

        // ROUTE_CHANGE fires on page load and browser back/forward ONLY. A move made inside the app (opening a
        // record, a breadcrumb, a menu) goes through the action service, which never triggers it, so the chips
        // stayed on the previous module. The action manager announces every UI update on the env bus; the router
        // applies its own push on a zero-delay timeout queued before that announcement, so one zero-delay hop of
        // ours reads the settled route. Only a CHANGED context costs a request.
        useBus(this.env.bus, "ACTION_MANAGER:UI-UPDATED", () => {
            browser.setTimeout(() => {
                if (this.state.enabled && this.state.open && this.moduleCtx !== this.lastCtx) {
                    this.refreshSuggestions().catch(() => {});
                }
            }, 0);
        });

        // Keep the transcript pinned to the latest message.
        useEffect(
            () => {
                const el = this.messagesRef.el;
                if (el) {
                    el.scrollTop = el.scrollHeight;
                }
            },
            () => [this.state.messages.length, this.state.sending, this.state.open]
        );
    }

    get toggleTitle() {
        // A JS expression inside t-att-title is not extracted for translation;
        // a getter built on _t() is.
        return this.state.open ? _t("Minimize the AI assistant") : _t("Open the AI assistant");
    }

    get moduleCtx() {
        // Best-effort: the router exposes the active model on record routes;
        // everywhere else the server's 'default' fallback applies.
        return router.current.model || "default";
    }

    async refreshState() {
        this.lastCtx = this.moduleCtx;
        const state = await this.orm.call("ncollection.ai.chat", "get_state", [], {
            module_ctx: this.moduleCtx,
        });
        this.state.enabled = Boolean(state.enabled);
        if (!this.state.enabled) {
            return;
        }
        this.state.freeText = Boolean(state.free_text);
        this.state.suggestions = state.suggestions || [];
        this.state.messages = state.history || [];
    }

    async refreshSuggestions() {
        this.lastCtx = this.moduleCtx;
        // include_history=false: this refresh only wants the chips, and the
        // server should not re-read 40 transcript rows to serve a navigation
        // event (an odoo-reviewer finding — the widget discarded them).
        const state = await this.orm.call("ncollection.ai.chat", "get_state", [], {
            module_ctx: this.moduleCtx,
            include_history: false,
        });
        if (state.enabled) {
            this.state.suggestions = state.suggestions || [];
        }
    }

    toggle() {
        this.state.open = !this.state.open;
        browser.localStorage.setItem(OPEN_STORAGE_KEY, this.state.open ? "1" : "0");
        if (this.state.open) {
            this.state.unread = false;
            this.refreshSuggestions().catch(() => {});
        }
    }

    renderBody(message) {
        return renderMarkdownSubset(message.body);
    }

    async _send(method, args) {
        if (this.state.sending) {
            return false;
        }
        this.state.sending = true;
        this.state.error = "";
        try {
            const result = await this.orm.call("ncollection.ai.chat", method, args);
            this.state.messages = this.state.messages.concat(result.messages || []);
            if (!this.state.open) {
                this.state.unread = true;
            }
            return true;
        } catch (error) {
            // A refusal (gate, #375 flag, secret scan, unknown key) arrives
            // as an RPC error. It is shown as a transient notice and NOT
            // appended to the transcript — the server persisted nothing, and
            // the client must not invent history the server refused to write.
            this.state.error =
                (error && error.data && error.data.message) ||
                (error && error.message) ||
                _t("The assistant could not answer.");
            return false;
        } finally {
            this.state.sending = false;
        }
    }

    async onSuggested(key) {
        await this._send("send_suggested", [key]);
    }

    async onSubmitTyped(ev) {
        ev.preventDefault();
        const question = this.state.typed.trim();
        if (!question) {
            return;
        }
        this.state.typed = "";
        const sent = await this._send("send_typed", [question]);
        if (!sent && !this.state.typed) {
            // A refusal is an EXPECTED flow here (the secret scan, the #375
            // flag, a gateway hiccup). Destroying a long typed question on
            // refusal punishes the user for the server doing its job — put
            // it back so it can be edited rather than retyped from nothing.
            this.state.typed = question;
        }
    }
}

registry.category("main_components").add("NcAiChatWidget", {
    Component: NcAiChatWidget,
});
