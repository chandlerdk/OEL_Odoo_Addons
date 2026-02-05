/** @odoo-module **/
import { registry } from "@web/core/registry";
import { formView } from "@web/views/form/form_view";
import { ControlPanel } from "@web/search/control_panel/control_panel";
import { useService } from "@web/core/utils/hooks";
import { FormRenderer } from '@web/views/form/form_renderer';
import { Component, onWillUnmount, useSubEnv, useEnv  } from "@odoo/owl";


export class FormControlPanelEbiz extends ControlPanel {
   setup() {
       this.controlPanelDisplay = {};
    }
}
export class EbizFormRenderer extends FormRenderer {
    setup() {
        super.setup();
        const env = useEnv();

        this.rpc = useService("rpc");
        if (this.env.model.root.resModel != 'transaction.header'){
            onWillUnmount(() => this._willUnmount());
        }
    }
    _willUnmount() {
        let appModels = ['upload.customers', 'upload.sale.orders', 'ebiz.upload.invoice', 'upload.products', 'upload.credit.notes', 'payment.request.bulk.email', 'payment.method.ui','batch.processing']
        if (appModels.indexOf(this.env.model.root.resModel) !== -1){
             const action = this.rpc("/web/dataset/call_kw/"+this.env.model.root.resModel+"/js_flush_customer", {
                    model: this.env.model.root.resModel,
                    method: 'js_flush_customer',
                    args : [[]],
                    kwargs: {}
             })
        }
    }
}

FormControlPanelEbiz.template = "payment_ebizcharge_crm.FormControlPanelEbiz";

export const FormControlPanelEbizView = {
    ...formView,
    ControlPanel: FormControlPanelEbiz,
    Renderer: EbizFormRenderer,
};

registry.category("views").add("ebiz_custom_form", FormControlPanelEbizView);
