/** @odoo-module **/
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { booleanField, BooleanField } from "@web/views/fields/boolean/boolean_field";

export class EBizBooleanToggleField extends BooleanField {
    static template = "payment_ebizcharge_crm.EBizBooleanToggleField";
    static props = {
        ...BooleanField.props,
        autosave: { type: Boolean, optional: true },
    };
    async onChange(newValue) {
        this.state.value = newValue;
        const changes = { [this.props.name]: newValue };
        await this.props.record.update(changes, { save: this.props.autosave });
    }
}
export const ebizbooleanToggleField = {
    ...booleanField,
    component: EBizBooleanToggleField,
};
registry.category("fields").add("boolean_toggle_ebiz", ebizbooleanToggleField);
