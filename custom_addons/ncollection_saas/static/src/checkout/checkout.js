/**
 * Public checkout behaviour (P2-T16, reworked for approval-gated registration).
 *
 * Plain frontend JS (no OWL): these pages render through a standalone layout,
 * not the backend web client. Talks to the type='jsonrpc' endpoints via fetch.
 * Each block guards on its page marker so one bundle serves pricing, the
 * registration form and the request-status page.
 *
 * Language: every visible string is looked up by key, in English or Arabic
 * (RTL). The server renders English as the fallback; this file switches the page
 * to the visitor's language (their saved choice, else the browser's) and offers
 * a toggle. Nothing about the visitor's language is sent to the server.
 */
(function () {
    "use strict";

    // ---------------------------------------------------------------- strings
    const STRINGS = {
        en: {
            "form.title": "Register your company",
            "form.sub": "Request a workspace on the",
            "form.sub2": "plan. We review every request before the workspace is created.",
            "f.company": "Company name",
            "f.contact": "Your name",
            "f.email": "Contact email",
            "f.email.hint": "For notices and password recovery. This is not your login.",
            "f.subdomain": "Workspace address (subdomain)",
            "f.username": "Username",
            "f.username.hint": "You sign in with this username, not your email. Lowercase letters, digits, . _ -",
            "f.password": "Password",
            "f.password2": "Confirm password",
            "f.submit": "Submit registration request",
            "pending.title": "Registration submitted",
            "pending.msg": "Your registration request has been submitted and is awaiting approval.",
            "pending.note": "We will review it shortly. Keep this page, or come back to it later.",
            "approved.title": "Request approved",
            "approved.msg": "Your request was approved. We are about to build your workspace.",
            "provisioning.title": "Building your workspace…",
            "provisioning.msg": "This usually takes a couple of minutes. You can keep this page open.",
            "ready.title": "Your workspace is ready",
            "ready.msg": "Sign in with the username and password you chose.",
            "ready.cta": "Open my workspace",
            "failed.title": "We are finishing your workspace",
            "failed.msg": "We hit a problem preparing your workspace. Our team has been notified and will retry.",
            "rejected.title": "Request not approved",
            "rejected.msg": "Your registration request was not approved. Please contact us for details.",
            "avail.ok": "✓ available",
            "avail.bad": "Not available.",
            "lang.toggle": "العربية",
        },
        ar: {
            "form.title": "سجّل شركتك",
            "form.sub": "اطلب مساحة عمل ضمن باقة",
            "form.sub2": ". نراجع كل طلب قبل إنشاء مساحة العمل.",
            "f.company": "اسم الشركة",
            "f.contact": "اسمك",
            "f.email": "البريد الإلكتروني للتواصل",
            "f.email.hint": "للإشعارات واستعادة كلمة المرور. وهو ليس اسم الدخول.",
            "f.subdomain": "عنوان مساحة العمل (النطاق الفرعي)",
            "f.username": "اسم المستخدم",
            "f.username.hint": "تدخل به إلى النظام بدلاً من بريدك الإلكتروني. أحرف إنجليزية صغيرة وأرقام و . _ -",
            "f.password": "كلمة المرور",
            "f.password2": "تأكيد كلمة المرور",
            "f.submit": "إرسال طلب التسجيل",
            "pending.title": "تم إرسال طلب التسجيل",
            "pending.msg": "تم إرسال طلب التسجيل الخاص بك وهو بانتظار الموافقة.",
            "pending.note": "سنراجعه قريباً. يمكنك الاحتفاظ بهذه الصفحة أو العودة إليها لاحقاً.",
            "approved.title": "تمت الموافقة على الطلب",
            "approved.msg": "تمت الموافقة على طلبك، وسنبدأ بإنشاء مساحة العمل.",
            "provisioning.title": "جارٍ تجهيز مساحة العمل…",
            "provisioning.msg": "يستغرق ذلك عادةً بضع دقائق. يمكنك إبقاء هذه الصفحة مفتوحة.",
            "ready.title": "مساحة العمل جاهزة",
            "ready.msg": "سجّل الدخول باسم المستخدم وكلمة المرور اللذين اخترتهما.",
            "ready.cta": "فتح مساحة العمل",
            "failed.title": "نُنهي تجهيز مساحة العمل",
            "failed.msg": "واجهتنا مشكلة أثناء التجهيز. تم إبلاغ فريقنا وسيعيد المحاولة.",
            "rejected.title": "لم تتم الموافقة على الطلب",
            "rejected.msg": "لم تتم الموافقة على طلب التسجيل. يُرجى التواصل معنا لمعرفة التفاصيل.",
            "avail.ok": "✓ متاح",
            "avail.bad": "غير متاح.",
            "lang.toggle": "English",
        },
    };

    // Server error keys -> message keys, so an error shown in Arabic stays Arabic.
    const ERRORS = {
        en: {
            missing_fields: "Please fill in the required fields.",
            field_too_long: "That entry is too long — please shorten it.",
            invalid_email: "That email address looks invalid.",
            username_is_email: "The username cannot be an email address. Choose a username such as atlasadmin.",
            invalid_username: "Username: 3–32 characters, lowercase letters, digits, . _ - , starting with a letter.",
            password_mismatch: "The two passwords do not match.",
            password_too_short: "The password must be at least 8 characters.",
            password_same_as_username: "The password cannot be the same as the username.",
            captcha_failed: "Captcha check failed — please try again.",
            subdomain_invalid: "Use 3–63 lowercase letters/digits, starting with a letter.",
            subdomain_reserved: "That workspace address is reserved. Try another.",
            subdomain_taken: "That workspace address is taken. Try another.",
            invalid_plan: "That plan is unavailable.",
            quota_exceeded: "Too many requests from here recently. Try again later.",
            rpc_error: "Something went wrong. Please try again.",
        },
        ar: {
            missing_fields: "يرجى تعبئة الحقول المطلوبة.",
            field_too_long: "القيمة المدخلة طويلة جداً، يرجى اختصارها.",
            invalid_email: "عنوان البريد الإلكتروني غير صحيح.",
            username_is_email: "لا يمكن أن يكون اسم المستخدم بريداً إلكترونياً. اختر اسماً مثل atlasadmin.",
            invalid_username: "اسم المستخدم: من 3 إلى 32 حرفاً إنجليزياً صغيراً أو أرقاماً أو . _ - ويبدأ بحرف.",
            password_mismatch: "كلمتا المرور غير متطابقتين.",
            password_too_short: "يجب ألا تقل كلمة المرور عن 8 أحرف.",
            password_same_as_username: "لا يمكن أن تكون كلمة المرور نفس اسم المستخدم.",
            captcha_failed: "فشل التحقق، يرجى المحاولة مرة أخرى.",
            subdomain_invalid: "استخدم من 3 إلى 63 حرفاً إنجليزياً صغيراً أو رقماً، على أن يبدأ بحرف.",
            subdomain_reserved: "هذا العنوان محجوز. جرّب عنواناً آخر.",
            subdomain_taken: "هذا العنوان مستخدم. جرّب عنواناً آخر.",
            invalid_plan: "هذه الباقة غير متاحة.",
            quota_exceeded: "طلبات كثيرة من هذا الجهاز مؤخراً. حاول لاحقاً.",
            rpc_error: "حدث خطأ ما. يرجى المحاولة مرة أخرى.",
        },
    };

    let lang = "en";
    try {
        const saved = window.localStorage.getItem("ncLang");
        lang = saved || ((navigator.language || "en").toLowerCase().startsWith("ar") ? "ar" : "en");
    } catch (e) { /* storage blocked: stay English */ }

    const t = (key) => (STRINGS[lang] && STRINGS[lang][key]) || STRINGS.en[key] || key;
    const err = (key) => (ERRORS[lang] && ERRORS[lang][key]) || ERRORS.en[key] || ERRORS.en.rpc_error;

    function applyLang() {
        document.documentElement.lang = lang;
        document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
        document.querySelectorAll("[data-i18n]").forEach((el) => {
            // Never overwrite an element the status page has already set itself.
            if (el.dataset.ncLocked) { return; }
            el.textContent = t(el.dataset.i18n);
        });
        const btn = document.querySelector("[data-nc-lang]");
        if (btn) { btn.textContent = t("lang.toggle"); }
    }

    function mountLangToggle() {
        const root = document.querySelector("[data-nc-checkout]");
        if (!root || document.querySelector("[data-nc-lang]")) { return; }
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "nc-checkout__lang";
        btn.setAttribute("data-nc-lang", "");
        btn.addEventListener("click", () => {
            lang = lang === "ar" ? "en" : "ar";
            try { window.localStorage.setItem("ncLang", lang); } catch (e) { /* ignore */ }
            applyLang();
            if (root.dataset.ncCheckout === "pending") { renderStage(root, root.__stage); }
        });
        document.body.insertBefore(btn, document.body.firstChild);
    }

    // ------------------------------------------------------------------- rpc
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

    function debounce(fn, ms) {
        let timer;
        return function () {
            const args = arguments;
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(null, args), ms);
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
                    hint.textContent = t("avail.ok");
                    hint.className = "nc-checkout__hint is-ok";
                } else {
                    hint.textContent = err("subdomain_" + r.reason) || t("avail.bad");
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
                email: fd.get("email"),
                subdomain: (fd.get("subdomain") || "").trim().toLowerCase(),
                username: (fd.get("username") || "").trim().toLowerCase(),
                password: fd.get("password"), password_confirm: fd.get("password_confirm"),
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
                    errBox.textContent = err(r.error);
                    btn.disabled = false;
                }
            } catch (e) {
                errBox.textContent = err("rpc_error");
                btn.disabled = false;
            }
        });
    }

    // ---- Request status page ----
    // pending -> approved -> provisioning -> ready, or failed / rejected.
    const STAGE_ICON = { pending: "◷", approved: "✓", provisioning: "◌", ready: "✓", failed: "!", rejected: "×" };

    function renderStage(root, r) {
        if (!r) { return; }
        root.__stage = r;
        const stage = r.stage || "pending";
        const title = root.querySelector("[data-nc-pending-title]");
        const msg = root.querySelector("[data-nc-pending-msg]");
        const login = root.querySelector("[data-nc-login]");
        const icon = root.querySelector("[data-nc-icon]");
        const company = root.querySelector("[data-nc-company]");
        [title, msg].forEach((el) => { el.dataset.ncLocked = "1"; });
        title.textContent = t(stage + ".title");
        msg.textContent = t(stage + ".msg");
        if (icon) {
            icon.textContent = STAGE_ICON[stage] || "";
            icon.className = "nc-checkout__icon is-" + stage;
        }
        if (company) {
            company.textContent = stage === "pending"
                ? (company.dataset.company || "") + " — " + t("pending.note") : (company.dataset.company || "");
        }
        if (stage === "ready" && r.login_url) {
            login.href = r.login_url;
            login.classList.remove("nc-checkout__cta--hidden");
        } else {
            login.classList.add("nc-checkout__cta--hidden");
        }
    }

    function initPending(root) {
        const uuid = root.dataset.tenantUuid;
        if (!uuid) { return; }
        async function poll() {
            let r;
            try { r = await jsonRpc("/nc/checkout/status", { tenant_uuid: uuid }); }
            catch (e) { return setTimeout(poll, 8000); }
            renderStage(root, r);
            // Terminal states stop polling; everything else keeps listening so
            // the page follows the request all the way to a ready workspace.
            if (r.stage === "ready" || r.stage === "rejected") { return; }
            setTimeout(poll, r.stage === "pending" ? 15000 : 4000);
        }
        poll();
    }

    function boot() {
        const root = document.querySelector("[data-nc-checkout]");
        if (!root) { return; }
        mountLangToggle();
        applyLang();
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
