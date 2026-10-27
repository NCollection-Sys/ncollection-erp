/** @odoo-module **/
/**
 * The Enterprise upsell is closed (models/res_config_settings.py hides the settings that carry it). This is the
 * safety net for any view that still renders one of the two upsell widgets: they never open the "Odoo Enterprise -
 * Upgrade now" dialog and never navigate anywhere; an upsell checkbox simply stays unticked, an upsell selection
 * keeps its value. Nothing is enabled or faked.
 */
import { patch } from "@web/core/utils/patch";
import { UpgradeBooleanField } from "@web/webclient/settings_form_view/fields/upgrade_boolean_field";

patch(UpgradeBooleanField.prototype, {
    async onChange() {
        if (odoo.info && odoo.info.isEnterprise) {
            return super.onChange(...arguments);
        }
        await this.props.record.update({ [this.props.name]: false });
    },
});
