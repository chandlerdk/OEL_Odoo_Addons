/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { AutoComplete } from "@web/core/autocomplete/autocomplete";

import { Component, onWillStart } from "@odoo/owl";

export class globalSearch extends Component {
    
    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.sources = [{
            options: this.loadOptionsSource.bind(this),
            optionTemplate: "GlobalSearchDropdownOption",
            placeholder: 'Loading...'
        }];
        this.placeholder = 'Global search for Records...'
    }
    
    async loadOptionsSource(ev) {
        var query = ev.inputValue || ev;
        try {
            var filter_data = await this.orm.call("global.search.model", "search_records", [0, query]);
            return filter_data;
        } catch (error) {
            const errorMsg = error?.data?.message || error.message || '';
            
            // Handle specific known errors
            if (errorMsg.includes('phone number must have at least 3 numbers')) {
                return [{
                    label: 'Please enter at least 3 digits for phone number searches',
                    model: false,
                }];
            }
            
            if (errorMsg.includes('AccessError') || errorMsg.includes('permission')) {
                return [{
                    label: 'You do not have permission to perform this search',
                    model: false,
                }];
            }
            
            // For other errors, log and return empty
            console.error('Global search error:', error);
            return [];
        }
    }
    
    async onSelect(option){
        if (option.model) {
            return await this.actionService.doAction({
                res_model: option.model,
                type: 'ir.actions.act_window',
                res_id : option.resId,
                views: [[false, 'form']]
            });
        }
    }
}

globalSearch.template = "sr_global_search";
globalSearch.components = {
    AutoComplete
};
globalSearch.props = {};

export const systrayItem = {
    Component: globalSearch,
};

registry.category("systray").add("sr_global_search.global_search", systrayItem, { sequence: 99 });
