/** @odoo-module **/
import { registry } from "@web/core/registry";
import { useInputField } from "@web/views/fields/input_field_hook";
import { Component, useRef,onWillUpdateProps, onWillStart, onPatched, useExternalListener} from "@odoo/owl";
import { useRecordObserver } from "@web/model/relational_model/utils";


export class FieldMaskingEbizDevice extends Component {
   setup(){
        super.setup();
        useInputField({ getValue: () => this.props.record._initialTextValues.emv_device_key || "", refName: "inputdate" })
        if (this.props.record._initialTextValues.emv_device_key != ''){
            this.inputRef = useRef("input");
            useInputField({ getValue: () => this.props.record._initialTextValues.emv_device_key|| "", refName: "inputdate" });
            this.props.record._initialTextValues.emv_device_key = this.props.record.data.emv_device_key
            var splittedValue = this.props.record.data.emv_device_key.match(/.{1,8}/g);
            var startValue = splittedValue[0];
            var endValue =splittedValue[splittedValue.length - 1];
            var finalValue = startValue.concat("_****_***_****_", endValue);
            this.props.record._initialTextValues.emv_device_key = finalValue;
            this.updateKey(this.props);
            onWillUpdateProps((nextProps) => this.updateKey(nextProps));
        }
        useExternalListener(window, "click", this.onWindowClick, true);
        onPatched(this.patched);
   }
   onWindowClick(ev) {
        if (this.props.record._initialTextValues.emv_device_key != ''){
        var splittedValue = this.props.record.data.emv_device_key.match(/.{1,8}/g);
        var startValue = splittedValue[0];
        var endValue = splittedValue[splittedValue.length - 1];
        var finalValue = startValue.concat("_****_***_****_", endValue);
        this.props.record._initialTextValues.emv_device_key=finalValue;
       }
   }
   patched() {
          if (this.props.record._initialTextValues.emv_device_key != ''){
            var splittedValue = this.props.record.data.emv_device_key.match(/.{1,8}/g);
            var startValue = splittedValue[0];
            var endValue = splittedValue[splittedValue.length - 1];
            var finalValue = startValue.concat("_****_***_****_", endValue);
            this.props.record._initialTextValues.emv_device_key=finalValue;
          }
          else {
                var splittedValue = this.props.record.data.emv_device_key.match(/.{1,8}/g);
                if (splittedValue){
                    var startValue = splittedValue[0];
                     var endValue = splittedValue[splittedValue.length - 1];
                    var finalValue = startValue.concat("_****_***_****_", endValue);
                    this.props.record._initialTextValues.emv_device_key=finalValue;
                }
                else{
                this.props.record._initialTextValues.emv_device_key='';
                }
          }
    }
   updateKey(props) {
       if (this.props.record._initialTextValues.emv_device_key != ''){
            var splittedValue = this.props.record.data.emv_device_key.match(/.{1,8}/g);
            var startValue = splittedValue[0];
            var endValue = splittedValue[splittedValue.length - 1];
            var finalValue = startValue.concat("_****_***_****_", endValue);
            props.value = finalValue;
            }
        }
}
FieldMaskingEbizDevice.template = 'payment_ebizcharge_crm.FieldMaskingDevice';
export const FieldMaskingEbizDevice1 = {
    ...Component,
    component: FieldMaskingEbizDevice,
};

registry.category("fields").add("eb_deviceaa_field_masking", FieldMaskingEbizDevice1);



