/** @odoo-module **/

import { patch } from '@web/core/utils/patch';
import { registry } from '@web/core/registry';

import { listView } from '@web/views/list/list_view';
import { ListRenderer } from '@web/views/list/list_renderer';

export class HideTooltipRenderer extends ListRenderer {
    setup() {
        super.setup();
    }
    getCellTitle(column, record) {
        return false
        }
}

HideTooltipRenderer.components = {
    ...HideTooltipRenderer.components,

}

registry.category('views').add('hide_tooltip', {
    ...listView,
    Renderer: HideTooltipRenderer,
});

