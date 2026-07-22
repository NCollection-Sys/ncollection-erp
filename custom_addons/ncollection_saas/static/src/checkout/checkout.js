/**
 * Public checkout behaviour (P2-T16) — plain frontend JS (no OWL): these pages
 * render through web.frontend_layout, not the backend web client. Talks to the
 * type='jsonrpc' endpoints via fetch. Each block guards on its page marker so
 * one bundle serves pricing, form and pending pages.
 */
(function () {
    "use strict";

    async function jsonRpc(url, params) {
        const res = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ jsonrpc: "2.0", method: "call", params: params || {}, id: Date.now() }),
        });
        const data = await res.json();
        if (data.error) {
            throw new Error((data.error.data && data.error.data.message) || "rpc_error");
        }
        return data.result;
    }

    // Stable error keys -> messages (bilingual-ready: swap for a lookup when
    // the i18n layer lands in P3-T08).
    const ERRORS = {
        missing_fields: "Please fill in the required fields.",
        invalid_email: "That email address looks invalid.",
        captcha_failed: "Captcha check failed — please try again.",
        subdomain_invalid: "Use 3–63 letters/numbers, starting with a letter.",
        subdomain_reserved: "That workspace address is reserved. Try another.",
        subdomain_taken: "That workspace address is taken. Try another.",
        invalid_plan: "That plan is unavailable.",
        quota_exceeded: "Too many trials from here recently. Try again later.",
        rpc_error: "Something went wrong. Please try again.",
    };

    function debounce(fn, ms) {
        let t;
        return function () {
            const args = arguments;
            clearTimeout(t);
            t = setTimeout(() => fn.apply(null, args), ms);
        };
    }

    // ---- Pricing: monthly/yearly toggle ----
    function initPricing(root) {
        const btns = root.querySelectorAll(".nc-checkout__toggle-btn");
        function apply(cycle) {
            btns.forEach((b) => b.classList.toggle("is-active", b.dataset.cycle === cycle));
            root.querySelectorAll(".nc-checkout__amount").forEach((el) => {
                const v = cycle === "yearly" ? el.dataset.yearly : el.dataset.monthly;
                el.textContent = Math.round(parseFloat(v || "0")).toString();
                const per = el.parentElement.querySelector(".nc-checkout__per");
                if (per) { per.textContent = cycle === "yearly" ? "/ yr" : "/ mo"; }
            });
            root.querySelectorAll(".nc-checkout__cta[data-plan]").forEach((a) => {
                a.href = "/nc/checkout?plan=" + encodeURIComponent(a.dataset.plan) + "&cycle=" + cycle;
            });
        }
        btns.forEach((b) => b.addEventListener("click", () => apply(b.dataset.cycle)));
    }

    // ---- Registration form ----
    function initForm(root) {
        const form = root.querySelector("[data-nc-form='register']");
        const sub = form.querySelector("input[name='subdomain']");
        const hint = form.querySelector("[data-nc-availability]");
        const errBox = form.querySelector("[data-nc-error]");
        const siteKey = root.dataset.recaptchaSiteKey || "";

        const check = debounce(async () => {
            const value = (sub.value || "").trim().toLowerCase();
            if (!value) { hint.textContent = ""; hint.className = "nc-checkout__hint"; return; }
            try {
                const r = await jsonRpc("/nc/checkout/availability", { subdomain: value });
                if (r.available) {
                    hint.textContent = "✓ available";
                    hint.className = "nc-checkout__hint is-ok";
                } else {
                    hint.textContent = ERRORS["subdomain_" + r.reason] || "Not available.";
                    hint.className = "nc-checkout__hint is-bad";
                }
            } catch (e) { hint.textContent = ""; }
        }, 350);
        sub.addEventListener("input", check);

        form.addEventListener("submit", async (ev) => {
            ev.preventDefault();
            errBox.textContent = "";
            const fd = new FormData(form);
            const params = {
                company: fd.get("company"), contact: fd.get("contact"),
                email: fd.get("email"), subdomain: (fd.get("subdomain") || "").trim().toLowerCase(),
                plan: fd.get("plan"), cycle: fd.get("cycle"),
            };
            if (siteKey && window.grecaptcha) {
                params.recaptcha_token = window.grecaptcha.getResponse();
            }
            const btn = form.querySelector("[data-nc-submit]");
            btn.disabled = true;
            try {
                const r = await jsonRpc("/nc/checkout/register", params);
                if (r.success) {
                    window.location.href = "/nc/checkout/pending/" + r.tenant_uuid;
                } else {
                    errBox.textContent = ERRORS[r.error] || ERRORS.rpc_error;
                    btn.disabled = false;
                }
            } catch (e) {
                errBox.textContent = ERRORS.rpc_error;
                btn.disabled = false;
            }
        });
    }

    // ---- Pending page: poll provisioning status ----
    function initPending(root) {
        const uuid = root.dataset.tenantUuid;
        const title = root.querySelector("[data-nc-pending-title]");
        const msg = root.querySelector("[data-nc-pending-msg]");
        const login = root.querySelector("[data-nc-login]");
        const spinner = root.querySelector("[data-nc-spinner]");
        if (!uuid) { return; }

        async function poll() {
            let r;
            try { r = await jsonRpc("/nc/checkout/status", { tenant_uuid: uuid }); }
            catch (e) { return setTimeout(poll, 5000); }
            if (r.verified && r.status === "provisioning") {
                title.textContent = "Building your workspace…";
                msg.textContent = "This usually takes a couple of minutes. You can keep this page open.";
            }
            if (r.status === "ready" && r.login_url) {
                if (spinner) { spinner.classList.add("is-done"); }
                title.textContent = "Your workspace is ready 🎉";
                msg.textContent = "Sign in to finish setting up your team.";
                login.href = r.login_url;
                login.classList.remove("nc-checkout__cta--hidden");
                return;
            }
            if (r.status === "error") {
                title.textContent = "Something went wrong";
                msg.textContent = "We hit a problem preparing your workspace. Our team has been notified.";
                if (spinner) { spinner.classList.add("is-error"); }
                return;
            }
            setTimeout(poll, 4000);
        }
        poll();
    }

    function boot() {
        const root = document.querySelector("[data-nc-checkout]");
        if (!root) { return; }
        const kind = root.dataset.ncCheckout;
        if (kind === "pricing") { initPricing(root); }
        else if (kind === "form") { initForm(root); }
        else if (kind === "pending") { initPending(root); }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
})();
