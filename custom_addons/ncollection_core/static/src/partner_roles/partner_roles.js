/** @odoo-module **/
/**
 * The partner dropdown of a document that opted in (nc.partner.roles.mixin) shows the partner's Customer / Vendor
 * badges through Odoo's own formatted display name (odoomark: `text` is a badge). odoomark draws EVERY badge with
 * the same class, so the two roles looked alike. The server sends the roles behind those badges, in the same order
 * (res.partner.web_name_search, ncollection_account_core), and this gives each badge its role class - the same
 * component as on the document form. The label stays Odoo's escaped markup: only the class of a badge odoomark
 * itself produced is changed, never any text.
 */
import { markup } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { Many2XAutocomplete } from "@web/views/fields/relational_utils";

const ROLE_CLASS = { customer: "o_nc_role_customer", vendor: "o_nc_role_vendor" };
// The class odoomark ends every badge with (web/static/src/core/utils/html.js); a user's text can never contain it,
// because odoomark HTML-escapes the text before it inserts its own markup.
const ODOOMARK_BADGE_END = 'o_tag_color_0">';

/**
 * ``label`` with its role badges classed. The role badges are the LAST ones (the server appends them after the
 * name), so a name that itself holds a back-tick pair keeps Odoo's own badge.
 */
export function roleBadges(label, roles) {
    if (!Array.isArray(roles) || !roles.length || typeof label === "string") {
        return label;
    }
    const html = String(label);
    const first = html.split(ODOOMARK_BADGE_END).length - 1 - roles.length;
    if (first < 0) {
        return label;
    }
    let index = 0;
    return markup(
        html.replaceAll(ODOOMARK_BADGE_END, (end) => {
            const role = index >= first ? ROLE_CLASS[roles[index - first]] : null;
            index++;
            return role ? `o_nc_role_badge ${role}">` : end;
        })
    );
}

patch(Many2XAutocomplete.prototype, {
    buildRecordSuggestion(request, record) {
        const suggestion = super.buildRecordSuggestion(...arguments);
        suggestion.label = roleBadges(suggestion.label, record.__nc_roles);
        return suggestion;
    },
});
