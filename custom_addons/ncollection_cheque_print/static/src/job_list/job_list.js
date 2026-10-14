/** @odoo-module **/
// "New" in Print Jobs opens the print wizard (choose the payment, the template, print) instead of an empty job form:
// a job is a snapshot made FROM a payment and a template, never typed in.
import { registry } from "@web/core/registry";
import { listView } from "@web/views/list/list_view";
import { ListController } from "@web/views/list/list_controller";

export class ChequeJobListController extends ListController {
    async createRecord() {
        await this.actionService.doAction("ncollection_cheque_print.action_nc_cheque_print_new", {
            onClose: () => this.model.load(),
        });
    }
}

registry.category("views").add("nc_cheque_job_list", {
    ...listView,
    Controller: ChequeJobListController,
});
