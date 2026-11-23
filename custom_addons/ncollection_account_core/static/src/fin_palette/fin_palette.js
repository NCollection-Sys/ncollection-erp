/** @odoo-module **/
// Request 09.2: the company's Financial Colours (session) on the document root; fin_palette.scss does the rest.
import { session } from "@web/session";

const palette = session.nc_fin_palette || "blue_red";
document.documentElement.dataset.ncFinPalette = palette;
