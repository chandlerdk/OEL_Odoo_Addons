/** @odoo-module **/
import { registry } from "@web/core/registry";
import { ListRenderer } from "@web/views/list/list_renderer";
import { useService } from "@web/core/utils/hooks";
import { X2ManyField, x2ManyField } from "@web/views/fields/x2many/x2many_field";


class UploadCreditNoteButtons extends ListRenderer {
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
         this.selection = isChecked ? this.props.list.records : [];
         const checkboxes = this.props.list.records
         this.props.allowSelectors=true;
         this.props.hasSelectors=true;
         this.props.activeActions.link=false;
         this.props.activeActions.unlink=false;
         const table = document.querySelector('.ui-sortable');
         const tablecheckboxes = table.querySelectorAll('.form-check-input');
         tablecheckboxes.forEach(function (inputb) {
            inputb.checked=isChecked;
         });
         checkboxes.forEach(function (checkbox) {
            checkbox.selected = isChecked;
         });
    }

    async UploadCreditNoteUpload (ev) {
            const res_ids = this.selection.map((record) => record.data);
            const hash = window.location.hash.substring(1);
            // Parse the hash into an object
            const params = Object.fromEntries(
              hash.split('&').map((param) => param.split('=').map(decodeURIComponent))
            );
             const action = await this.rpc("/web/dataset/call_kw/upload.credit.notes/upload_invoice", {
                model: 'upload.credit.notes',
                method: 'upload_invoice',
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

    async UploadCreditNoteExportList (ev) {
            const res_ids = this.selection.map((record) => record.data);
            const hash = window.location.hash.substring(1);
            // Parse the hash into an object
            const params = Object.fromEntries(
              hash.split('&').map((param) => param.split('=').map(decodeURIComponent))
            );
             const action = await this.rpc("/web/dataset/call_kw/upload.credit.notes/export_orders", {
                model: 'upload.credit.notes',
                method: 'export_orders',
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

    async UploadCreditNoteClearLogs (ev) {
            const res_ids = this.selection.map((record) => record.data);
            const hash = window.location.hash.substring(1);
            // Parse the hash into an object
            const params = Object.fromEntries(
              hash.split('&').map((param) => param.split('=').map(decodeURIComponent))
            );
             const action = await this.rpc("/web/dataset/call_kw/upload.credit.notes/clear_logs", {
                model: 'upload.credit.notes',
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

    async UploadCreditNoteExportLogs (ev) {
            const res_ids = this.selection.map((record) => record.data);
            const hash = window.location.hash.substring(1);
            // Parse the hash into an object
            const params = Object.fromEntries(
              hash.split('&').map((param) => param.split('=').map(decodeURIComponent))
            );
             const action = await this.rpc("/web/dataset/call_kw/upload.credit.notes/export_logs", {
                model: 'upload.credit.notes',
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

UploadCreditNoteButtons.template = "payment_ebizcharge_crm.UploadCreditNoteButtons";
export class UploadCreditNoteButtonsX2ManyField extends X2ManyField {
    setup() {
        super.setup();
    }
}

UploadCreditNoteButtonsX2ManyField.components = { ...X2ManyField.components, ListRenderer: UploadCreditNoteButtons };

export const uploadCreditNoteButtonsX2ManyField = {
    ...x2ManyField,
    component: UploadCreditNoteButtonsX2ManyField,
    additionalClasses: [...x2ManyField.additionalClasses || [], "o_field_one2many"],
};
registry.category("fields").add("upload_credit_notes_buttons", uploadCreditNoteButtonsX2ManyField);
