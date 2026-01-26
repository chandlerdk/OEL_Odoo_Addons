/** @odoo-module **/
import { registry } from "@web/core/registry";
import { ListRenderer } from "@web/views/list/list_renderer";
import { useService } from "@web/core/utils/hooks";
import { X2ManyField, x2ManyField } from "@web/views/fields/x2many/x2many_field";
const { Component, onWillUnmount } = owl;

class UploadCustomersButtons extends ListRenderer {
    // setup method
    setup() {
        super.setup();
        this.selection = [];
        this.props.allowSelectors=true;
        this.props.hasSelectors=true;
        this.props.activeActions.link=false;
        this.props.activeActions.unlink=false;
        this.action = useService("action");
        this.rpc = useService("rpc");
    }

    get selectAll() {
        const list = this.props.list;
        this.props.allowSelectors=true;
        this.props.hasSelectors=true;
        this.props.activeActions.link=false;
        this.props.activeActions.unlink=false;
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
         this.props.allowSelectors=true;
         this.props.hasSelectors=true;
         this.props.activeActions.link=false;
        this.props.activeActions.unlink=false;
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


    async UploadCustomerUpload (ev) {
            const res_ids = this.selection.map((record) => record.data);
            const hash = window.location.hash.substring(1);
            // Parse the hash into an object
            const params = Object.fromEntries(
              hash.split('&').map((param) => param.split('=').map(decodeURIComponent))
            );
             const action = await this.rpc("/web/dataset/call_kw/upload.customers/upload_customers", {
                model: 'upload.customers',
                method: 'upload_customers',
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
    async UploadCustomerDeactivate (ev) {
            const res_ids = this.selection.map((record) => record.data);
            const hash = window.location.hash.substring(1);
            // Parse the hash into an object
            const params = Object.fromEntries(
              hash.split('&').map((param) => param.split('=').map(decodeURIComponent))
            );
             const action = await this.rpc("/web/dataset/call_kw/upload.customers/delete_customers", {
                model: 'upload.customers',
                method: 'delete_customers',
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
    async UploadCustomerExportList (ev) {
            const res_ids = this.selection.map((record) => record.data);
            const hash = window.location.hash.substring(1);
            // Parse the hash into an object
            const params = Object.fromEntries(
              hash.split('&').map((param) => param.split('=').map(decodeURIComponent))
            );
             const action = await this.rpc("/web/dataset/call_kw/upload.customers/export_customers", {
                model: 'upload.customers',
                method: 'export_customers',
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
    async UploadCustomerClearLogs (ev) {
            const res_ids = this.selection.map((record) => record.data);
            const hash = window.location.hash.substring(1);
            // Parse the hash into an object
            const params = Object.fromEntries(
              hash.split('&').map((param) => param.split('=').map(decodeURIComponent))
            );
             const action = await this.rpc("/web/dataset/call_kw/upload.customers/clear_logs", {
                model: 'upload.customers',
                method: 'clear_logs',
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
    async UploadCustomerExportLogs (ev) {
            const res_ids = this.selection.map((record) => record.data);
            const hash = window.location.hash.substring(1);
            // Parse the hash into an object
            const params = Object.fromEntries(
              hash.split('&').map((param) => param.split('=').map(decodeURIComponent))
            );
             const action = await this.rpc("/web/dataset/call_kw/upload.customers/export_logs", {
                model: 'upload.customers',
                method: 'export_logs',
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

UploadCustomersButtons.template = "payment_ebizcharge_crm.UploadCustomersButtons";
export class UploadCustomersButtonsX2ManyField extends X2ManyField {
    setup() {
        super.setup();
    }
}

UploadCustomersButtonsX2ManyField.components = { ...X2ManyField.components, ListRenderer: UploadCustomersButtons };

export const uploadCustomersButtonsX2ManyField = {
    ...x2ManyField,
    component: UploadCustomersButtonsX2ManyField,
    additionalClasses: [...x2ManyField.additionalClasses || [], "o_field_one2many"],
};

registry.category("fields").add("upload_customers_buttons", uploadCustomersButtonsX2ManyField);
