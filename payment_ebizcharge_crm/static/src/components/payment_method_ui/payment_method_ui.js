/** @odoo-module **/
import { registry } from "@web/core/registry";
import { ListRenderer } from "@web/views/list/list_renderer";
import { useService } from "@web/core/utils/hooks";
import { X2ManyField, x2ManyField } from "@web/views/fields/x2many/x2many_field";
const { Component } = owl; // Removed 'useEffect' as it's not needed

class PaymentMethodUi extends ListRenderer {
    // setup method
    setup() {
        super.setup();
        this.selection = [];
        this.props.allowSelectors=true;
        this.props.hasSelectors=true;
        this.action = useService("action");
        this.rpc = useService("rpc");
    }

    get selectAll() {
        const list = this.props.list;
        this.props.allowSelectors=true;
        this.props.hasSelectors=true;
        this.selection = [];
        return false;
    }

    toggleRecordSelection(record) {
         const isSelected = record.selected;
         this.props.allowSelectors=true;
         this.props.hasSelectors=true;
         this.props.activeActions.link=false;
         this.props.activeActions.unlink=false;
         let is_on_click = false;
         let is_hover_click = false;
         if (event.currentTarget.checked){
             console.log('test');
             is_on_click = true;
         }
         else{
            const inp_obj = event.currentTarget.querySelector(".form-check-input");
            if (inp_obj){
               if (inp_obj.checked){
                  is_hover_click = true;
               }
               else {
                  is_hover_click = false;
               }
            }
         }
         if(record.selected){
            this.selection = this.selection.filter((r)=> r.id!==record.id )
            record.selected=false;
         }
         else{
              if (is_on_click==true || is_hover_click==true){
                  this.selection.push(record);
                  record.selected=true;
              }
            }
    }

    toggleSelection() {
         const checkbox = event.currentTarget;
         const isChecked = checkbox.checked;
         console.log('test00')
         this.props.allowSelectors=true;
         this.props.hasSelectors=true;
         this.selection = isChecked ? this.props.list.records : [];
         const checkboxes = this.props.list.records;

         const table = document.querySelector('.ui-sortable');
         const tablecheckboxes = table.querySelectorAll('.form-check-input');
         tablecheckboxes.forEach(function (inputb) {
            inputb.checked=isChecked;
         });
         checkboxes.forEach(function (checkbox) {
            checkbox.selected = isChecked;
         });
    }

    async sendRequest () {
         const res_ids = this.selection.map((record) => record.data);
         const hash = window.location.hash.substring(1);
         // Parse the hash into an object
         const params = Object.fromEntries(
          hash.split('&').map((param) => param.split('=').map(decodeURIComponent))
         );

         const action = await this.rpc("/web/dataset/call_kw/payment.method.ui/send_request_payment", {
            model: 'payment.method.ui',
            method: 'send_request_payment',
            args : [[parseInt(params['id'])]],
            kwargs: {"values":res_ids}
         })
         await this.env.services.action.doAction(action, {
            onClose: () => {
            this.selection = []
            this.action.loadState(); // Reload the component
            }
        });
    }

    async resendRequest () {
             const res_ids = this.selection.map((record) => record.data);
             const hash = window.location.hash.substring(1);
             // Parse the hash into an object
             const params = Object.fromEntries(
              hash.split('&').map((param) => param.split('=').map(decodeURIComponent))
             );

             const action = await this.rpc("/web/dataset/call_kw/payment.method.ui/resend_email", {
                model: 'payment.method.ui',
                method: 'resend_email',
                args : [[parseInt(params['id'])]],
                kwargs: {"values":res_ids}
             })
             await this.env.services.action.doAction(action, {
                onClose: () => {
                this.selection = []
                this.action.loadState(); // Reload the component
                }
            });
    }

    async removePendingList () {
             const res_ids = this.selection.map((record) => record.data);
             const hash = window.location.hash.substring(1);
             // Parse the hash into an object
             const params = Object.fromEntries(
              hash.split('&').map((param) => param.split('=').map(decodeURIComponent))
             );

             const action = await this.rpc("/web/dataset/call_kw/batch.processing/delete_invoice", {
                model: 'payment.method.ui',
                method: 'delete_invoice',
                args : [[parseInt(params['id'])]],
                kwargs: {"values":res_ids}
             })
             await this.env.services.action.doAction(action, {
                onClose: () => {
                this.selection = []
                this.action.loadState(); // Reload the component
                }
            });
    }

    async removeAddedList () {
             const res_ids = this.selection.map((record) => record.data);
             const hash = window.location.hash.substring(1);
             // Parse the hash into an object
             const params = Object.fromEntries(
              hash.split('&').map((param) => param.split('=').map(decodeURIComponent))
             );

             const action = await this.rpc("/web/dataset/call_kw/payment.method.ui/delete_invoice_added", {
                model: 'payment.method.ui',
                method: 'delete_invoice_added',
                args : [[parseInt(params['id'])]],
                kwargs: {"values":res_ids}
             })
             await this.env.services.action.doAction(action, {
                onClose: () => {
                this.selection = []
                this.action.loadState(); // Reload the component
                }
            });
    }
}

PaymentMethodUi.template = "payment_ebizcharge_crm.PaymentMethodUI";
export class PaymentMethodUiX2ManyField extends X2ManyField {
    setup() {
        super.setup();
    }
}

PaymentMethodUiX2ManyField.components = { ...X2ManyField.components, ListRenderer: PaymentMethodUi };

export const paymentMethodUiX2ManyField = {
    ...x2ManyField,
    component: PaymentMethodUiX2ManyField,
    additionalClasses: [...x2ManyField.additionalClasses || [], "o_field_one2many"],
};


registry.category("fields").add("payment_method_ui", paymentMethodUiX2ManyField);
