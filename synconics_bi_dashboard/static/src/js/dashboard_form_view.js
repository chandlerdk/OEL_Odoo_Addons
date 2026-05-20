/** @odoo-module **/
import { FormCompiler } from "@web/views/form/form_compiler";
import { formView } from "@web/views/form/form_view";
import { registry } from "@web/core/registry";
// import { patch } from "@web/core/utils/patch";

export class BiDashboardFormViewCompiler extends FormCompiler {
  compileForm(el, params) {
    const res = super.compileForm(el, params);
    const classes = res.getAttribute("t-attf-class");
    const formView = res.getElementsByClassName("o_form_sheet_bg")[0];
    if (formView) {
      formView.classList.add("customBottom");
      const formParent = formView.parentElement;

      if (formParent) {
        const chatter = formParent.querySelector(".o-mail-Form-chatter");
        if (chatter) {
          chatter.classList.add("customBottom");
        }
      }

      const newClasses = classes.replace(
        '{{ __comp__.uiService.size < 6 ? "flex-column" : "flex-nowrap h-100" }}',
        "flex-column",
      );
      res.setAttribute("t-attf-class", `${newClasses}`);
    }

    return res;
  }

  compile(node, params) {
    const res = super.compile(node, params);

    const chatterContainerHookXml = res.querySelector(".o-mail-Form-chatter");
    if (!chatterContainerHookXml) {
      return res;
    }

    const classes = chatterContainerHookXml.getAttribute("t-attf-class");
    if (classes) {
      const newClasses = classes.replace(
        '{{ __comp__.uiService.size >= 6 ? "o-aside" : "mt-4 mt-md-0" }}',
        "mt-4 mt-md-0",
      );
      res.setAttribute("t-attf-class", `${newClasses}`);
    }
    return res;
  }
}

export const BiDashboardFormView = {
  ...formView,
  Compiler: BiDashboardFormViewCompiler,
};

registry
  .category("views")
  .add("synconics_bi_dashboard_form_view", BiDashboardFormView);
