/** @odoo-module **/
import { registry } from "@web/core/registry";
import { ListRenderer } from "@web/views/list/list_renderer";
import { useService } from "@web/core/utils/hooks";
import { X2ManyField, x2ManyField } from "@web/views/fields/x2many/x2many_field";
const { Component } = owl; // Removed 'useEffect' as it's not needed
import { uniqueId } from "@web/core/utils/functions";


class TransactionButtons extends ListRenderer {
    // setup method
    setup() {
        super.setup();
        this.selection = [];
        this.props.allowSelectors=true;
        this.props.hasSelectors=true;
        this.action = useService("action");
        this.rpc = useService("rpc");
        this.props.uniqueRendererClass = uniqueId("o_list_renderer_");

    }

    get selectAll() {
        const list = this.props.list;
        this.props.allowSelectors=true;
        this.props.hasSelectors=true;
        this.props.uniqueRendererClass = uniqueId("o_list_renderer_");
        this.selection = [];
        return false;
    }

    toggleRecordSelection(record) {
         const isSelected = record.selected;
         this.props.allowSelectors=true;
         this.props.hasSelectors=true;
         this.props.activeActions.link=false;
         this.props.activeActions.unlink=false;
        this.props.uniqueRendererClass = uniqueId("o_list_renderer_");
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
         this.props.allowSelectors=true;
        this.props.uniqueRendererClass = uniqueId("o_list_renderer_");
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
    async showHistoryExport (ev) {
            const res_ids = this.selection.map((record) => record.data);
            const hash = window.location.hash.substring(1);
            // Parse the hash into an object
            const params = Object.fromEntries(
              hash.split('&').map((param) => param.split('=').map(decodeURIComponent))
            );
             const action = await this.rpc("/web/dataset/call_kw/transaction.header/export_transactions", {
                model: 'transaction.header',
                method: 'export_transactions',
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

    // Credit Button Event handler for the transaction button click
    async showHistoryCredit () {
             const res_ids = this.selection.map((record) => record.data);
             const hash = window.location.hash.substring(1);
             // Parse the hash into an object
             const params = Object.fromEntries(
              hash.split('&').map((param) => param.split('=').map(decodeURIComponent))
             );

             const action = await this.rpc("/web/dataset/call_kw/transaction.header/credit_or_void", {
                model: 'transaction.header',
                method: 'credit_or_void',
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

    // Email Event handler for the transaction button click
    async showHistoryEmail () {
             const res_ids = this.selection.map((record) => record.data);
             const hash = window.location.hash.substring(1);
             // Parse the hash into an object
             const params = Object.fromEntries(
              hash.split('&').map((param) => param.split('=').map(decodeURIComponent))
             );
             const action = await this.rpc("/web/dataset/call_kw/transaction.header/action_open_email_wizard", {
                model: 'transaction.header',
                method: 'action_open_email_wizard',
                args : [[parseInt(params['id'])]],
                kwargs: {"values":res_ids}
             })
             await this.env.services.action.doAction(action, {
                onClose: () => {
                this.selection = []
                this.action.loadState();
//                this.actionService.loadState(); // Reload the component
//                this.actionService.doAction({
//                    type: "ir.actions.act_window",
//                    res_model: 'transaction.header',
//                    views: [[this.formViewId, "form"]],
////                    res_id: 1,
//                    });
                }
            });
    }

}

TransactionButtons.template = "payment_ebizcharge_crm.TransactionButtons";
export class TransactionButtonsX2ManyField extends X2ManyField {
    setup() {
        super.setup();
    }
}

TransactionButtonsX2ManyField.components = { ...X2ManyField.components, ListRenderer: TransactionButtons };

export const transactionButtonsX2ManyField = {
    ...x2ManyField,
    component: TransactionButtonsX2ManyField,
    additionalClasses: [...x2ManyField.additionalClasses || [], "o_field_one2many"],
};

registry.category("fields").add("transaction_buttons", transactionButtonsX2ManyField);
