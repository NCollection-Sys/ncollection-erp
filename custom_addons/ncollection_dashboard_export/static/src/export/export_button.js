/** @odoo-module **/
import { Component } from "@odoo/owl";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { waitForDataLoaded } from "@spreadsheet/helpers/model";
import { Model } from "@odoo/o-spreadsheet";

const MAX_COLS = 60;
const MAX_ROWS = 1000;

/** One evaluated cell as the page shows it: ["n", number, shown] | ["s", text, shown] | null. */
function snapshotCell(getters, sheetId, col, row) {
    const cell = getters.getEvaluatedCell({ sheetId, col, row });
    if (!cell || cell.value === null || cell.value === undefined || cell.value === "") {
        return null;
    }
    const shown = String(cell.formattedValue ?? cell.value);
    if (typeof cell.value === "number" && Number.isFinite(cell.value)) {
        return ["n", cell.value, shown];
    }
    return ["s", String(cell.value), shown];
}

function snapshotSheets(getters) {
    const sheets = [];
    for (const sheetId of getters.getSheetIds()) {
        if (getters.isSheetVisible && !getters.isSheetVisible(sheetId)) {
            continue;
        }
        const cols = Math.min(getters.getNumberCols(sheetId), MAX_COLS);
        const rows = Math.min(getters.getNumberRows(sheetId), MAX_ROWS);
        const data = [];
        for (let row = 0; row < rows; row++) {
            const line = [];
            for (let col = 0; col < cols; col++) {
                line.push(snapshotCell(getters, sheetId, col, row));
            }
            while (line.length && line[line.length - 1] === null) {
                line.pop();
            }
            if (line.length) {
                data.push(line);
            }
        }
        sheets.push({ name: getters.getSheetName(sheetId), rows: data });
    }
    return sheets;
}

/** Scorecards and charts: the values the figures show, as label / value lines. */
function snapshotFigures(getters) {
    const figures = [];
    for (const sheetId of getters.getSheetIds()) {
        for (const figure of getters.getFigures(sheetId)) {
            try {
                const chartId = getters.getChartIdFromFigureId
                    ? getters.getChartIdFromFigureId(figure.id)
                    : figure.id;
                if (!chartId) {
                    continue;
                }
                const definition = getters.getChartDefinition(chartId);
                const runtime = getters.getChartRuntime(chartId);
                const title = definition?.title?.text || "";
                const lines = [];
                if (runtime?.chartJsConfig?.data) {
                    const { labels = [], datasets = [] } = runtime.chartJsConfig.data;
                    lines.push(["", ...datasets.map((d) => d.label ?? "")]);
                    labels.forEach((label, i) =>
                        lines.push([label, ...datasets.map((d) => d.data?.[i] ?? "")])
                    );
                } else if (runtime && (runtime.keyValue !== undefined || runtime.gaugeValue)) {
                    lines.push([title, runtime.keyValue ?? runtime.gaugeValue?.label ?? ""]);
                    if (runtime.baselineDisplay) {
                        lines.push(["", runtime.baselineDisplay]);
                    }
                }
                if (lines.length) {
                    figures.push({ title, lines });
                }
            } catch {
                // a figure kind this build cannot describe is left out of the file, never guessed
            }
        }
    }
    return figures;
}

/** A spreadsheet date serial (days since 1899-12-30) as YYYY-MM-DD. */
function serialToDate(serial) {
    return new Date(Math.round((serial - 25569) * 86400000)).toISOString().slice(0, 10);
}

function filterPart(filter, part) {
    const value = part?.formattedValue ?? part?.value ?? part;
    return filter.type === "date" && typeof value === "number" ? serialToDate(value) : String(value ?? "");
}

function snapshotFilters(getters) {
    const out = [];
    try {
        for (const filter of getters.getGlobalFilters()) {
            const shown = getters.getFilterDisplayValue(filter.label);
            const parts = Array.isArray(shown) ? shown.flat() : [shown];
            out.push(`${filter.label}: ${parts.map((p) => filterPart(filter, p)).filter(Boolean).join(" → ")}`);
        }
    } catch {
        // filters are context only; the figures are what matter
    }
    return out;
}

/**
 * "Export" beside Share on the Dashboards page: Excel and PDF of what the page shows right now (the same cells, the
 * same global filters). The files are built on the server from a validated snapshot of the page - see
 * controllers/export.py.
 */
export class NcDashboardExportButton extends Component {
    static template = "ncollection_dashboard_export.ExportButton";
    static components = { Dropdown, DropdownItem };
    static props = {
        model: { type: Model, optional: true },
        dashboardId: { type: [Number, Boolean], optional: true },
        togglerClass: { type: String, optional: true },
    };

    get togglerClass() {
        return ["btn", this.props.togglerClass].join(" ");
    }

    async onExport(format) {
        const model = this.props.model;
        if (!model || !this.props.dashboardId) {
            return;
        }
        await waitForDataLoaded(model);
        const getters = model.getters;
        const snapshot = {
            filters: snapshotFilters(getters),
            sheets: snapshotSheets(getters),
            figures: snapshotFigures(getters),
        };
        // a real form POST: the browser downloads the attachment, the page stays where it is
        const form = document.createElement("form");
        form.method = "POST";
        form.action = `/ncollection/dashboards/export/${format}`;
        form.style.display = "none";
        const fields = {
            csrf_token: odoo.csrf_token,
            dashboard_id: String(this.props.dashboardId),
            payload: JSON.stringify(snapshot),
        };
        for (const [name, value] of Object.entries(fields)) {
            const input = document.createElement("input");
            input.type = "hidden";
            input.name = name;
            input.value = value;
            form.appendChild(input);
        }
        document.body.appendChild(form);
        form.submit();
        form.remove();
    }
}
