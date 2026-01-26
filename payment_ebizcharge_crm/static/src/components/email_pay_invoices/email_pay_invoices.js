/** @odoo-module **/
import { registry } from "@web/core/registry";
import { ListRenderer } from "@web/views/list/list_renderer";
import { X2ManyField, x2ManyField } from "@web/views/fields/x2many/x2many_field";
import { useService } from "@web/core/utils/hooks";
const { Component } = owl; // Removed 'useEffect' as it's not needed

class EmailPayInvoices extends ListRenderer {
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
         this.props.allowSelectors=true;
        this.props.hasSelectors=true;
         this.selection = isChecked ? this.props.list.records : [];
         const checkboxes = this.props.list.records
         const table = document.querySelector('.ui-sortable');
         const tablecheckboxes = table.querySelectorAll('.form-check-input');
         tablecheckboxes.forEach(function (inputb) {
            inputb.checked=isChecked;
         });
         checkboxes.forEach(function (checkbox) {
            checkbox.selected = isChecked;
         });
    }


    // Export Event handler for the transaction button click
    async sendEmailPayRequest (ev) {
            const res_ids = this.selection.map((record) => record.data);
            const hash = window.location.hash.substring(1);
            // Parse the hash into an object
            const params = Object.fromEntries(
              hash.split('&').map((param) => param.split('=').map(decodeURIComponent))
            );
             const action = await this.rpc("/web/dataset/call_kw/payment.request.bulk.email/process_invoices", {
                model: 'payment.request.bulk.email',
                method: 'process_invoices',
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
    async ResendEmailPayRequest (ev) {
            const res_ids = this.selection.map((record) => record.data);
            const hash = window.location.hash.substring(1);
            // Parse the hash into an object
            const params = Object.fromEntries(
              hash.split('&').map((param) => param.split('=').map(decodeURIComponent))
            );
             const action = await this.rpc("/web/dataset/call_kw/payment.request.bulk.email/resend_email", {
                model: 'payment.request.bulk.email',
                method: 'resend_email',
                args: [parseInt(params['id'])],
                kwargs: {"values":res_ids},
            })
            await this.env.services.action.doAction(action, {
                onClose: () => {
                this.selection = []
                this.action.loadState(); // Reload the component
                }
            });
    }
    async DeletePendingEmailPayRequest (ev) {
            const res_ids = this.selection.map((record) => record.data);
            const hash = window.location.hash.substring(1);
            // Parse the hash into an object
            const params = Object.fromEntries(
              hash.split('&').map((param) => param.split('=').map(decodeURIComponent))
            );
             const action = await this.rpc("/web/dataset/call_kw/payment.request.bulk.email/delete_invoice", {
                model: 'payment.request.bulk.email',
                method: 'delete_invoice',
                args: [parseInt(params['id'])],
                kwargs: {"values":res_ids},
            })
            await this.env.services.action.doAction(action, {
                onClose: () => {
                this.selection = []
                this.action.loadState(); // Reload the component
                }
            });
    }
    async ApplyReceivedEmailPayRequest (ev) {
            const res_ids = this.selection.map((record) => record.data);
            const hash = window.location.hash.substring(1);
            // Parse the hash into an object
            const params = Object.fromEntries(
              hash.split('&').map((param) => param.split('=').map(decodeURIComponent))
            );
             const action = await this.rpc("/web/dataset/call_kw/payment.request.bulk.email/mark_applied", {
                model: 'payment.request.bulk.email',
                method: 'mark_applied',
                args: [parseInt(params['id'])],
                kwargs: {"values":res_ids},
            })
            await this.env.services.action.doAction(action, {
                onClose: () => {
                this.selection = []
                this.action.loadState(); // Reload the component
                }
            });
    }
    async DeleteReceivedEmailPayRequest (ev) {
            const res_ids = this.selection.map((record) => record.data);
            const hash = window.location.hash.substring(1);
            // Parse the hash into an object
            const params = Object.fromEntries(
              hash.split('&').map((param) => param.split('=').map(decodeURIComponent))
            );
             const action = await this.rpc("/web/dataset/call_kw/payment.request.bulk.email/delete_invoice_2", {
                model: 'payment.request.bulk.email',
                method: 'delete_invoice_received',
                args: [parseInt(params['id'])],
                kwargs: {"values":res_ids},
            })
            await this.env.services.action.doAction(action, {
                onClose: () => {
                this.selection = []
                this.action.loadState(); // Reload the component
                }
            });
    }



}

EmailPayInvoices.template = "payment_ebizcharge_crm.EmailPayInvoices";

export class EmailPayInvoicesX2ManyField extends X2ManyField {
    setup() {
        super.setup();
    }
}

EmailPayInvoicesX2ManyField.components = { ...X2ManyField.components, ListRenderer: EmailPayInvoices };

export const emailPayInvoicesX2ManyField = {
    ...x2ManyField,
    component: EmailPayInvoicesX2ManyField,
    additionalClasses: [...x2ManyField.additionalClasses || [], "o_field_one2many"],
};

registry.category("fields").add("email_pay_invoices", emailPayInvoicesX2ManyField);
