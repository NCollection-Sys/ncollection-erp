/** @odoo-module **/
import { registry } from "@web/core/registry";
import { localization } from "@web/core/l10n/localization";
import { FileViewer } from "@web/core/file_viewer/file_viewer";
import { FileModel } from "@web/core/file_viewer/file_model";

/**
 * Odoo's own FileViewer (modal, images with zoom/rotate, PDF, text, video, download, close, previous/next
 * with the arrow keys), extended with what the sales list needs and the stock viewer lacks:
 *  - "2 / 5" position and the file name in the header (the name is bidi-isolated: Arabic file names),
 *  - a clear fallback card, with a download button, for a file type the browser cannot preview,
 *  - right-to-left behaviour: the arrows and the arrow KEYS follow the reading direction.
 * Nothing here talks to the server: it only renders the metadata it is given.
 */
export class NcFileViewer extends FileViewer {
    static template = "ncollection_sale_attachments.NcFileViewer";

    get isRtl() {
        return localization.direction === "rtl";
    }

    get position() {
        return `${this.state.index + 1} / ${this.props.files.length}`;
    }

    /** Previewable inline by the stock viewer (anything else gets the fallback card). */
    get canPreview() {
        const file = this.state.file;
        return !!(file.isImage || file.isPdf || file.isText || file.isVideo || file.isUrlYoutube);
    }

    onKeydown(ev) {
        // In a right-to-left UI "next" is to the LEFT: swap the two arrow keys.
        if (this.isRtl && (ev.key === "ArrowRight" || ev.key === "ArrowLeft")) {
            ev.preventDefault();
            if (ev.key === "ArrowRight") {
                this.previous();
            } else {
                this.next();
            }
            return;
        }
        super.onKeydown(ev);
    }
}

/** A plain-data attachment (as returned by sale.order.nc_get_attachments) as the viewer's file model. */
export function toViewerFile(data) {
    return Object.assign(new FileModel(), {
        id: data.id,
        name: data.name,
        mimetype: data.mimetype,
        checksum: data.checksum,
        type: data.type,
        url: data.url,
    });
}

let viewerId = 1;

/** Opens ONE modal with all the files (starting at ``startIndex``); returns ``close``. */
export function openNcFileViewer(files, startIndex = 0) {
    const key = `ncollection_sale_attachments.viewer${viewerId++}`;
    const close = () => registry.category("main_components").remove(key);
    registry.category("main_components").add(key, {
        Component: NcFileViewer,
        props: { files, startIndex, close },
    });
    return close;
}
