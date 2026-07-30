# NCollection Arabic (ar_001) Terminology Glossary — P3-T08

The canonical Arabic rendering of NCollection's core domain terms. Every
`ncollection_*` module's `i18n/ar.po` uses these **consistently** — the same
concept is translated the same way everywhere. Modern Standard Arabic (MSA),
suitable for a UAE business audience.

> **Status of the translations shipped with P3-T08:** *engineering-complete
> drafts* authored against these terms. They still require (1) **native
> linguistic review** and (2) **visual staging validation** in a live RTL
> instance before they can be called production-ready. See the P3-T08 PR's
> human-review checklist.

## Core domain terms

| English | Arabic | Notes |
|---|---|---|
| Subscription | اشتراك | plan subscription |
| Subscription Plan | خطة الاشتراك | |
| Plan | الخطة | |
| Tenant | مستأجر | the SaaS customer/workspace owner |
| Workspace | مساحة العمل | |
| Trial | فترة تجريبية | "Trial" status: تجريبي |
| Billing | الفوترة | |
| Invoice | فاتورة | |
| Payment | الدفع | |
| Provisioning | التجهيز | database provisioning |
| Approval | موافقة | approval workflow: سير الموافقات |
| Dashboard | لوحة المعلومات | |
| Dunning | مطالبات السداد | overdue-payment reminders |
| Renewal | التجديد | |
| Suspension / Suspended | تعليق / معلّق | |
| Expiry / Expired | انتهاء الصلاحية / منتهي | |
| Grace period | فترة السماح | |

## Status values (selection labels)

| English | Arabic |
|---|---|
| Draft | مسودة |
| Active | نشط |
| Pending | قيد الانتظار |
| Done | تم |
| Failed | فشل |
| Cancelled | ملغى |
| Expired | منتهي الصلاحية |
| Suspended | معلّق |
| Ready | جاهز |
| Running | قيد التشغيل |
| Monthly | شهري |
| Yearly | سنوي |

## Common UI / accounting terms

| English | Arabic |
|---|---|
| Company | الشركة |
| Contact | جهة الاتصال |
| Email | البريد الإلكتروني |
| Status | الحالة |
| Database | قاعدة البيانات |
| Domain / Subdomain | النطاق / النطاق الفرعي |
| Currency | العملة |
| Price | السعر |
| Monthly Price | السعر الشهري |
| Yearly Price | السعر السنوي |
| User / Users | مستخدم / المستخدمون |
| Role | الدور |
| VAT | ضريبة القيمة المضافة |
| Login | تسجيل الدخول |
| Settings | الإعدادات |
| Create | إنشاء |

## P3-T08 completion states & remaining human items

The three states are **separate** — engineering completion does **not** imply the
other two:

1. **Engineering completion — DONE (this PR).**
   - RTL audit + fix (logical CSS properties everywhere) + a CI conformance guard.
   - `i18n/ar.po` authored for every user-facing `ncollection_*` module (~550
     entries), verbatim source msgids, glossary-consistent Arabic, structurally
     validated in CI (UTF-8, grammar, no empty translations).
   - Arabic-PDF: audited — glyphs already render on the stock image; financial
     PDF font/RTL stays with `ncollection_account_localization_uae` (#49).

2. **Native linguistic review — REQUIRED, not done.**
   The Arabic is an engineering-quality MSA draft. A native reviewer must verify
   tone/terminology on: the long help texts, the lifecycle/marketing copy, and
   any domain nuances. Track corrections against this glossary.

3. **Visual staging validation — REQUIRED, not done.**
   On a staging tenant with `ar_001` active: run `odoo --i18n-export` to confirm
   msgid fidelity (hand-authored QWeb/mail-body fragments were deliberately left
   to this round-trip), switch the UI to Arabic, and walk the core flows
   (login, dashboard, settings, checkout, subscription/billing screens) checking
   for zero broken RTL layouts and that translations apply.

### Deliberately deferred to the staging export round-trip
Tag-split QWeb / mail-template **body fragments** (text broken across inline
`<t t-out/>` / `<strong>` nodes) are not hand-authored here — their exact msgids
(with Odoo's whitespace normalization) are only reliable from `--i18n-export`.
Field labels, menus, selections, buttons, errors, and clean single-node text
ARE translated. Fill the remaining body fragments from the exported `.pot`.
