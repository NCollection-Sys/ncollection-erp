/** @odoo-module **/
// Drag and drop of a statement file onto a bank journal card or the bank matching workspace.
//
// This is only a way to HAND a file to the existing import wizard. The server method action_nc_bank_drop re-checks
// access, the journal type, the file type and the size, then opens the same wizard the Upload button opens, with the
// file in place. Nothing is parsed, previewed or imported by a drop: the user still presses Preview, reads the totals
// and presses Import. The checks here (one file, type, size) only save a round trip.
import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";

const EXTENSIONS = [".csv", ".xlsx", ".xml", ".camt", ".qif", ".ofx", ".qfx", ".zip"];
const MAX_BYTES = 10 * 1024 * 1024;
const ACTIVE = "o_nc_drop_active";

function carriesFiles(event) {
    return Array.from(event.dataTransfer?.types || []).includes("Files");
}

function readAsBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
    });
}

export const bankDropService = {
    dependencies: ["orm", "action", "notification"],
    start(env, { orm, action, notification }) {
        // Where a dropped file may land: the journal card (its id is on a hidden marker the card template carries)
        // or the workspace of one journal (the id is in the action context the card opened it with).
        function targetOf(event) {
            const element = event.target instanceof Element ? event.target : null;
            if (!element) {
                return null;
            }
            const card = element.closest(".o_kanban_record");
            const hint = card && card.querySelector(".o_nc_bank_drop_hint[data-journal-type='bank']");
            if (hint) {
                return { journalId: parseInt(hint.dataset.journalId, 10), zone: card };
            }
            const current = action.currentController?.action;
            if (current?.res_model === "account.bank.statement.line") {
                const journalId = current.context?.default_journal_id;
                const zone = element.closest(".o_action_manager > .o_action");
                if (journalId && zone) {
                    return { journalId, zone };
                }
            }
            return null;
        }

        function clear() {
            document.querySelectorAll("." + ACTIVE).forEach((el) => el.classList.remove(ACTIVE));
        }

        document.addEventListener("dragover", (event) => {
            if (!carriesFiles(event)) {
                return;
            }
            const target = targetOf(event);
            clear();
            if (target) {
                event.preventDefault();
                target.zone.classList.add(ACTIVE);
            }
        });
        document.addEventListener("dragleave", (event) => {
            if (!event.relatedTarget) {
                clear();
            }
        });
        document.addEventListener("drop", async (event) => {
            if (!carriesFiles(event)) {
                return;
            }
            const target = targetOf(event);
            clear();
            if (!target) {
                return;
            }
            event.preventDefault();
            const files = Array.from(event.dataTransfer.files);
            if (files.length !== 1) {
                notification.add(_t("Drop one statement file at a time."), { type: "danger" });
                return;
            }
            const file = files[0];
            if (!EXTENSIONS.some((ext) => file.name.toLowerCase().endsWith(ext))) {
                notification.add(
                    _t("%(file)s is not a bank statement file. Accepted: %(types)s.", {
                        file: file.name,
                        types: EXTENSIONS.join(", "),
                    }),
                    { type: "danger" }
                );
                return;
            }
            if (file.size > MAX_BYTES) {
                notification.add(
                    _t("%(file)s is larger than %(limit)s MB, the most that can be imported at once.", {
                        file: file.name,
                        limit: MAX_BYTES / (1024 * 1024),
                    }),
                    { type: "danger" }
                );
                return;
            }
            const content = await readAsBase64(file);
            const wizard = await orm.call("account.journal", "action_nc_bank_drop", [
                [target.journalId],
                file.name,
                content,
            ]);
            await action.doAction(wizard);
        });
    },
};

registry.category("services").add("ncollection_bank_drop", bankDropService);
