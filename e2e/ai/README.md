# AI chat widget (P5-T06 / #63) - browser acceptance

`fixtures/run_ai_web.sh up` builds a **private** stack (own Postgres container, the repo's mock-provider `satellites/ai_gateway`, Odoo from this
working tree) on its own Docker network and publishes Odoo on `127.0.0.1:8169`. It uses neither the shared database server nor any shared
service, no external AI provider can be reached (provider `mock`), and the gateway signing key is generated per run and kept only in the
environment of the two disposable containers. `OCA_DIR` defaults to `<repo>/oca` (mounted read-only). `run_ai_web.sh clean` removes everything.

`acceptance_chat.js` (headless Edge via `../dashboards/lib.js`) - 13 checks, each followed by an ORM read: plain user gets nothing (UI and server),
accountant sees bubble/panel/chips/hint, a raw `send_typed` is refused while free text is off, a suggestion round trip is persisted in order,
reload restores the transcript, the renderer cannot create any element outside `p/ul/li/strong/code` or any attribute from hostile model output,
390 px English and Arabic RTL layouts fit, Arabic chips/title/hint are Arabic, a second accountant sees nothing and cannot forge a row, free text on
shows typed hostile text as text (no element, no handler, no dialog), a secret-looking question is refused with the text kept, no page/console errors.

Run: `bash fixtures/run_ai_web.sh up` then `node acceptance_chat.js` (from `e2e/`), then `bash fixtures/run_ai_web.sh clean`.
The suite flips `ncollection_ai.enable_free_text_questions` on the disposable database as `admin` and resets it at the end.
