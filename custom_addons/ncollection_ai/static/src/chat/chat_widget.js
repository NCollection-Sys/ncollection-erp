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
import { registry } from "@web/core/registry";
import { useBus, useService } from "@web/core/utils/hooks";

const OPEN_STORAGE_KEY = "nc_ai_chat_open";

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
        // it does not recognise.)
        useBus(routerBus, "ROUTE_CHANGE", () => {
            if (this.state.enabled && this.state.open) {
                this.refreshSuggestions();
            }
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

    get moduleCtx() {
        // Best-effort: the router exposes the active model on record routes;
        // everywhere else the server's 'default' fallback applies.
        return router.current.model || "default";
    }

    async refreshState() {
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
        const state = await this.orm.call("ncollection.ai.chat", "get_state", [], {
            module_ctx: this.moduleCtx,
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
            this.refreshSuggestions();
        }
    }

    renderBody(message) {
        return renderMarkdownSubset(message.body);
    }

    async _send(method, args) {
        if (this.state.sending) {
            return;
        }
        this.state.sending = true;
        this.state.error = "";
        try {
            const result = await this.orm.call("ncollection.ai.chat", method, args);
            this.state.messages = this.state.messages.concat(result.messages || []);
            if (!this.state.open) {
                this.state.unread = true;
            }
        } catch (error) {
            // A refusal (gate, #375 flag, secret scan, unknown key) arrives
            // as an RPC error. It is shown as a transient notice and NOT
            // appended to the transcript — the server persisted nothing, and
            // the client must not invent history the server refused to write.
            this.state.error =
                (error && error.data && error.data.message) ||
                (error && error.message) ||
                "The assistant could not answer.";
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
        await this._send("send_typed", [question]);
    }
}

registry.category("main_components").add("NcAiChatWidget", {
    Component: NcAiChatWidget,
});
