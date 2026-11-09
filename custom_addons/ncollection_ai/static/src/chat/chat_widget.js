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
            closing: false,
            freeText: false,
            suggestions: [],
            messages: [],
            sending: false,
            streaming: false,
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
        if (this.state.closing) {
            return;
        }
        if (this.state.open) {
            this.close();
            return;
        }
        this.state.open = true;
        browser.localStorage.setItem(OPEN_STORAGE_KEY, "1");
        this.state.unread = false;
        this.refreshSuggestions().catch(() => {});
        // focus follows the panel: the typed input when there is one, else the first suggestion
        setTimeout(() => {
            document.querySelector(".nc-ai-chat__input, .nc-ai-chat__chip")?.focus();
        }, 60);
    }

    close() {
        browser.localStorage.setItem(OPEN_STORAGE_KEY, "0");
        const reduced = browser.matchMedia && browser.matchMedia("(prefers-reduced-motion: reduce)").matches;
        const finish = () => {
            this.state.open = false;
            this.state.closing = false;
            document.querySelector(".nc-ai-chat__bubble")?.focus();
        };
        if (reduced) {
            finish();
            return;
        }
        this.state.closing = true;      // the panel plays its exit animation, then is removed
        setTimeout(finish, 180);
    }

    onPanelKeydown(ev) {
        if (ev.key === "Escape") {
            ev.stopPropagation();
            this.close();
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

    /**
     * STREAMED delivery (P5-T06 acceptance: "streaming responses").
     *
     * POSTs to /ncollection/ai/chat/stream with Odoo's own CSRF token and reads
     * the newline-delimited JSON as it arrives: each "delta" extends a pending
     * assistant bubble, "done" swaps the pending bubbles for the two rows the
     * server saved, "error" removes them (nothing was saved). The pending text
     * goes through the same escape-first renderer as any message.
     *
     * Returns true (answered), false (refused / failed, error shown) or
     * "fallback" when this browser or deployment cannot stream - the caller
     * then uses the batch RPC, so the widget keeps working either way.
     */
    async _stream(kind, value, shown) {
        if (this.state.sending) {
            return false;
        }
        if (!browser.fetch || typeof TextDecoder === "undefined") {
            return "fallback";
        }
        this.state.sending = true;
        this.state.error = "";
        this.state.streaming = false;
        const userKey = "pending-user";
        const botKey = "pending-bot";
        const dropPending = () => {
            this.state.messages = this.state.messages.filter(
                (m) => m.id !== userKey && m.id !== botKey
            );
        };
        try {
            const response = await browser.fetch("/ncollection/ai/chat/stream", {
                method: "POST",
                credentials: "same-origin",
                headers: { "Content-Type": "application/x-www-form-urlencoded" },
                body: new URLSearchParams({
                    kind,
                    value: value || "",
                    csrf_token: odoo.csrf_token || "",
                }),
            });
            if ([404, 405, 501].includes(response.status) || !response.body) {
                return "fallback";
            }
            if (!response.ok) {
                // An admission refusal (gate, #375 flag, secret scan, budget):
                // a normal JSON error, decided before anything was streamed.
                let message = "";
                try {
                    message = (await response.json()).message;
                } catch {
                    // not JSON: the generic notice below
                }
                throw new Error(message || _t("The assistant could not answer."));
            }
            this.state.messages = this.state.messages.concat([
                { id: userKey, role: "user", body: shown },
                { id: botKey, role: "assistant", body: "" },
            ]);
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";
            let finished = false;
            const handle = (line) => {
                if (!line.trim()) {
                    return;
                }
                const event = JSON.parse(line);
                if (event.type === "delta") {
                    this.state.streaming = true;
                    const bot = this.state.messages.find((m) => m.id === botKey);
                    if (bot) {
                        bot.body += event.text;
                    }
                } else if (event.type === "done") {
                    dropPending();
                    this.state.messages = this.state.messages.concat(event.messages || []);
                    finished = true;
                } else if (event.type === "error") {
                    throw new Error(event.message);
                }
            };
            for (;;) {
                const { value: chunk, done } = await reader.read();
                if (done) {
                    break;
                }
                buffer += decoder.decode(chunk, { stream: true });
                const lines = buffer.split("\n");
                buffer = lines.pop();
                lines.forEach(handle);
            }
            handle(buffer);
            if (!finished) {
                throw new Error(_t("The assistant could not answer."));
            }
            if (!this.state.open) {
                this.state.unread = true;
            }
            return true;
        } catch (error) {
            dropPending();
            this.state.error =
                (error && error.message) || _t("The assistant could not answer.");
            return false;
        } finally {
            this.state.sending = false;
            this.state.streaming = false;
        }
    }

    async _deliver(kind, value, shown) {
        const outcome = await this._stream(kind, value, shown);
        if (outcome === "fallback") {
            return this._send(kind === "suggested" ? "send_suggested" : "send_typed", [value]);
        }
        return outcome;
    }

    async onSuggested(key) {
        const chip = this.state.suggestions.find((c) => c.key === key);
        await this._deliver("suggested", key, chip ? chip.label : key);
    }

    async onSubmitTyped(ev) {
        ev.preventDefault();
        const question = this.state.typed.trim();
        if (!question) {
            return;
        }
        this.state.typed = "";
        const sent = await this._deliver("typed", question, question);
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
