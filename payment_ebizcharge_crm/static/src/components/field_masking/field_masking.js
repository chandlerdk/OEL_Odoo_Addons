/** @odoo-module **/
import { registry } from "@web/core/registry";
import { useInputField } from "@web/views/fields/input_field_hook";
import { Component, useRef,onWillUpdateProps, onWillStart, onPatched, useExternalListener} from "@odoo/owl";
import { useRecordObserver } from "@web/model/relational_model/utils";


export class FieldMaskingEbizA extends Component {
   setup(){
        super.setup();
        useInputField({ getValue: () => this.props.record._initialTextValues.ebiz_security_key || "", refName: "inputdate" })
        if (this.props.record._initialTextValues.ebiz_security_key != ''){
            this.inputRef = useRef("input");
            useInputField({ getValue: () => this.props.record._initialTextValues.ebiz_security_key|| "", refName: "inputdate" });
            this.props.record._initialTextValues.ebiz_security_key = this.props.record.data.ebiz_security_key
            var splittedValue = this.props.record.data.ebiz_security_key.match(/.{1,8}/g);
            var startValue = splittedValue[0];
            var endValue =splittedValue[splittedValue.length - 1];
            var finalValue = startValue.concat("_****_***_****_", endValue);
            this.props.record._initialTextValues.ebiz_security_key = finalValue;
            this.updateKey(this.props);
            onWillUpdateProps((nextProps) => this.updateKey(nextProps));
        }
        useExternalListener(window, "click", this.onWindowClick, true);
        onPatched(this.patched);
   }
   onWindowClick(ev) {
        if (this.props.record._initialTextValues.ebiz_security_key != ''){
        var splittedValue = this.props.record.data.ebiz_security_key.match(/.{1,8}/g);
        var startValue = splittedValue[0];
        var endValue = splittedValue[splittedValue.length - 1];
        var finalValue = startValue.concat("_****_***_****_", endValue);
        this.props.record._initialTextValues.ebiz_security_key=finalValue;
       }
   }
   patched() {
          if (this.props.record._initialTextValues.ebiz_security_key != ''){
            var splittedValue = this.props.record.data.ebiz_security_key.match(/.{1,8}/g);
            var startValue = splittedValue[0];
            var endValue = splittedValue[splittedValue.length - 1];
            var finalValue = startValue.concat("_****_***_****_", endValue);
            this.props.record._initialTextValues.ebiz_security_key=finalValue;
          }
          else {
                var splittedValue = this.props.record.data.ebiz_security_key.match(/.{1,8}/g);
                if (splittedValue){
                    var startValue = splittedValue[0];
                     var endValue = splittedValue[splittedValue.length - 1];
                    var finalValue = startValue.concat("_****_***_****_", endValue);
                    this.props.record._initialTextValues.ebiz_security_key=finalValue;
                }
                else{
                this.props.record._initialTextValues.ebiz_security_key='';
                }
          }
    }
   updateKey(props) {
       if (this.props.record._initialTextValues.ebiz_security_key != ''){
            var splittedValue = this.props.record.data.ebiz_security_key.match(/.{1,8}/g);
            var startValue = splittedValue[0];
            var endValue = splittedValue[splittedValue.length - 1];
            var finalValue = startValue.concat("_****_***_****_", endValue);
            props.value = finalValue;
            }
        }
}
FieldMaskingEbizA.template = 'payment_ebizcharge_crm.FieldMasking';
export const FieldMaskingEbizA1 = {
    ...Component,
    component: FieldMaskingEbizA,
};

registry.category("fields").add("security_field_masking", FieldMaskingEbizA1);



