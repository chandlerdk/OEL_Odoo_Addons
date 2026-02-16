/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

const { xml } = owl;

class ContactUsPage extends Component {}
ContactUsPage.template = "payment_ebizcharge_crm.contact_us_page";

registry.category("actions").add("contact_us_page", ContactUsPage);