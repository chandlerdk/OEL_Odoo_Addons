/** @odoo-module **/

import { registry } from "@web/core/registry";
import { usePopover } from "@web/core/popover/popover_hook";
import { Component, EventBus, onWillRender } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { localization } from "@web/core/l10n/localization";

export class HelpPopover extends Component {}

HelpPopover.template = "payment_ebizcharge_crm.HelpPopOvertemplate";

export class HelpPopoverWidget extends Component {
    setup() {
        const position = localization.direction === "rtl" ? "bottom" : "left";
        this.popover = usePopover(HelpPopover, { position });
    }
    showPopup(ev) {
           console.log('in 1')
        this.popover.open(ev.currentTarget, {
            });
    }
    closePopup(ev) {
          console.log('in 2')
        this.closePopover();
    }

    closePopover() {
        console.log('in 3')
        this.popover.close();
    }
}

HelpPopoverWidget.components = { Popover: HelpPopover };
HelpPopoverWidget.template = "payment_ebizcharge_crm.buttonhelp";
export const helppopoverWidget = {
    component: HelpPopoverWidget,
};
registry.category("view_widgets").add("help_popover_widget", helppopoverWidget);
