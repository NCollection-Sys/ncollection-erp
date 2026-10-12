/** @odoo-module **/
import { Component, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { openNcFileViewer, toViewerFile } from "../viewer/nc_file_viewer";

/**
 * List-view paperclip for any record that has attachments (sale.order today).
 *  - no attachment : nothing is rendered (no disabled or empty control),
 *  - one or more   : a paperclip with the count; a click loads the METADATA of that record's attachments
 *                    (never the content) and opens ONE modal viewer on them.
 * The server call runs as the user, so it returns only what that user may read.
 */
export class AttachmentCountField extends Component {
    static template = "ncollection_sale_attachments.AttachmentCountField";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        onWillUnmount(() => this.closeViewer?.());      // leaving the list closes its viewer
    }

    get title() {
        return _t("Show attachments");
    }

    get count() {
        return this.props.record.data[this.props.name] || 0;
    }

    async onClick(ev) {
        ev.stopPropagation();            // never open the record under the icon
        ev.preventDefault();
        if (this.loading) {
            return;                      // a double click must not stack two viewers
        }
        this.loading = true;
        try {
            const record = this.props.record;
            const rows = await this.orm.call(record.resModel, "nc_get_attachments", [record.resId]);
            if (!rows.length) {
                this.notification.add(_t("No attachments."), { type: "info" });
                return;
            }
            this.closeViewer?.();
            this.closeViewer = openNcFileViewer(rows.map(toViewerFile), 0);
        } finally {
            this.loading = false;
        }
    }
}

registry.category("fields").add("nc_attachment_count", {
    component: AttachmentCountField,
    supportedTypes: ["integer"],
});
