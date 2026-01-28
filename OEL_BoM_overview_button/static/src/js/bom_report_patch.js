/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { BomReportView } from "@mrp/views/bom_report/bom_report_view";
import { onWillStart, onMounted } from "@odoo/owl";

patch(BomReportView.prototype, {
    setup() {
        super.setup();
        
        console.log("BoM Overview Patch Loaded");

        onWillStart(() => {
            // Set the state before the component even starts rendering
            if (this.state) {
                this.state.activeReportView = "availabilities";
            }
        });

        onMounted(() => {
            // Double check after mount to ensure UI reflects the state
            if (this.state && this.state.activeReportView !== "availabilities") {
                this.state.activeReportView = "availabilities";
            }
        });
    },
});
