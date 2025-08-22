/* @odoo-module */

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const commandSetupRegistry = registry.category("command_setup");
const commandProviderRegistry = registry.category("command_provider");

// ----
// add @ namespace + provider
// ----
commandSetupRegistry.add("%", {
    debounceDelay: 200,
    emptyMessage: _t("No record found"),
    name: _t("records"),
    placeholder: _t("Search for Record..."),
});

commandProviderRegistry.add("global.search.model", {
    namespace: "%",

    async provide(env, options) {
        const commands = [];
        try {
            var filter_data = await env.services.orm.call("global.search.model", "search_records", [0, options.searchValue]);
            filter_data.forEach((option) => {
                if (option.model) {
                    commands.push({
                        action() {
                            env.services.action.doAction({
                                res_model: option.model,
                                type: 'ir.actions.act_window',
                                res_id : option.resId,
                                views: [[false, 'form']]
                            })
                        },
                        name: option.model_name + '->' + option.label,
                    });
                }
            });
        } catch (error) {
            console.error('Command palette search error:', error);
        }
        return commands;
    },
});
