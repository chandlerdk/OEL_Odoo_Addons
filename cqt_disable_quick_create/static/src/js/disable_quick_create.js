/** @odoo-module **/

/**
Here in this module Create and Edit functionality is manipulated of any Many2one field.
In v17 it's different from previous versions. There is a flag in ir.model form view "Disabling the Create and Edit option".
For which model this flag is true, quick create and edit option should be disabled for them.

In previous versions, it's done by overriding "computeActiveActions" and change the value of this.state.activeActions
depending on the fields model name.
But in V17 it's done by getting the flag value from backend just before component start. And instead of getting all models
for which the flag is true, flag value is fetched from backend for every model separately.
**/

import { patch } from "@web/core/utils/patch";
import { Many2OneField } from "@web/views/fields/many2one/many2one_field";
import { useService } from "@web/core/utils/hooks";
const { onWillStart } = owl;

patch(Many2OneField.prototype, {
    setup() {
        this.orm = useService("orm");

        onWillStart(async ()=> {
            const modelName = this.props.record.fields[this.props.name].relation;
            if(modelName){
                const modelPerm = await this.orm.searchRead('ir.model', [['model','=', modelName]], ["model", "disable_create_edit"]);

                if (modelPerm && modelPerm[0].disable_create_edit){
                    this.state.activeActions = {
                        create: false,
                        createEdit: false,
                        write: false,
                    };
                    this.quickCreate = undefined;
                }
            }
        });
        super.setup(...arguments);
    },
});
