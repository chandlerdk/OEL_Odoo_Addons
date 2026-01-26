/** @odoo-module **/

import { Component } from '@odoo/owl';
import publicWidget from '@web/legacy/js/public/public_widget';
import { browser } from '@web/core/browser/browser';
import { ConfirmationDialog } from '@web/core/confirmation_dialog/confirmation_dialog';
import { _t } from '@web/core/l10n/translation';
import { renderToMarkup } from '@web/core/utils/render';
import { RPCError } from '@web/core/network/rpc_service';

publicWidget.registry.eBizPaymentForm = publicWidget.Widget.extend({
    selector: '#o_ebiz_payment_form',
    events: Object.assign({}, publicWidget.Widget.prototype.events, {
        'click [name="ebiz_submit_button"]': '_ebizSubmitButton',
        'click [name="ebiz_cancel_button"]': '_ebizCancelButton',
        'click [name="update_ebiz_card"]': '_updateEbizCard',
        'click [name="update_ebiz_account"]': '_updateEbizAccount',
        'click #card-tab': '_addNewEbizCard',
        'click #account-tab': '_addNewEbizAccount',
        'click button[name="delete_ebiz_pm"]': '_deletePmEvent',
        'click button[name="refresh_payment_tokens"]': '_refreshPaymentMethods',
    }),


    // #=== WIDGET LIFECYCLE ===#

    /**
     * @override
     */
    init() {
        this._super(...arguments);
        this.rpc = this.bindService("rpc");
        this.orm = this.bindService("orm");
    },


    /**
     * Display an error dialog.
     *
     * @private
     * @param {string} title - The title of the dialog.
     * @param {string} errorMessage - The error message.
     * @return {void}
     */
    _displayErrorDialog(title, errorMessage = '') {
        this.call('dialog', 'add', ConfirmationDialog, { title: title, body: errorMessage || "" });
    },

     /**
     * @private
     * @param {MouseEvent} ev
     */
    _deletePmEvent(ev) {
        var record_id = parseInt(ev.currentTarget.value);
        this.call("dialog", "add", ConfirmationDialog, {
            title: _t("Delete Record"),
            body: _t("Are you sure you want to delete this record?"),
            confirmLabel: _t("Delete"),
            confirm: async () => {
                /**
                 * Calls 'unlink' method on payment.token to delete the record and
                 * return page after deletion to re-arrange the content on UI
                 */
                await this.orm.unlink("payment.token", [record_id]);
                return window.location = '/my/ebiz_payment_method';
            },
            cancel: () => {},
        });
    },


    _disableButton(blockUI = false) {
        Component.env.bus.trigger('disablePaymentButton');
        if (blockUI) {
            this.call('ui', 'block');
        }
    },


    _enableButton(unblockUI = true) {
        Component.env.bus.trigger('enablePaymentButton');
        if (unblockUI) {
            this.call('ui', 'unblock');
        }
    },



   /**
     * @private
     */
async _ebizCancelButton(ev)  {
        return window.location = '/my/ebiz_payment_method';
    },


   /**
     * @private
     */
async _ebizSubmitButton(ev)  {
        const return_Value = await this._getPaymentDetails(document.getElementById('ebiz_provider_id').value)
        var button = document.getElementById('add_new_ebiz_card');
        if (return_Value){
        button.disabled = true;
        this.rpc('/payment/ebizcharge/s2s/create_json_3ds',{kwargs: await this._getPaymentDetails(document.getElementById('ebiz_provider_id').value)}
        ).then(ebizProcessingValues =>  {
             if (ebizProcessingValues){
             $('#add_new_ebiz_card').prop('disabled', true);
                 button.disabled = false;
                 return window.location = '/my/ebiz_payment_method';
             }
        }).catch(error => {
            if (error instanceof RPCError) {
                this._displayErrorDialog(_t("Payment processing failed"), error.data.message);
                button.disabled = false; // The button has been disabled before initiating the flow.
            } else {
                return Promise.reject(error);
            }
        });
        }

 },

         /**
     * @private
     */
     async _refreshPaymentMethods(ev) {
        this.rpc('/refresh_payment_profiles').then(function (result) {
                alert("Payment methods are up to date!")
                location.reload()
            });
    },
    
      /**
     * @private
     */
    async _getAcquirerTypeFromCheckbox (element) {
        return element[0].id;
    },

/**
 * Return all relevant inline form inputs based on the payment method type of the acquirer.
 *
 * @private
 * @param {number} acquirerId - The id of the selected acquirer
 * @return {Object} - An object mapping the name of inline form inputs to their DOM element
 */
    async _getInlineFormInputs (acquirerId) {
        var tab = $('.nav-link.active');
        var acquirerType = document.getElementById('new_account_tab_id').value;
        var authorizeOnly = this.$el.find("input[name='authorizeOnly']")[0]
            if (acquirerType === 'new_account'){
                return {
                    accountName: document.getElementById('bank_account_holder_name'),
                    accountNumber: document.getElementById('account_number'),
                    routingNumber: document.getElementById('routing_number'),
                    tokenAccBox: document.getElementById('token_save_box_acc'),
                    accountType: document.getElementById('bank_account_type'),
                    pmid: document.getElementById('update_pm_id'),
                    default_card_method: document.getElementById('default_card_method'),
                    ismanageScreen: document.getElementById('is_ebiz_manage_screen'),
                };
            }else if (acquirerType === 'new_card'){
                return {
                    card: document.getElementById('cc_number'),
                    name: document.getElementById('cc_holder_name'),
                    street: document.getElementById('avs_street'),
                    zip: document.getElementById('avs_zip'),
                    expiry: document.getElementById('cc_expiry'),
                    tokenBox: document.getElementById('token_save_box_credit'),
                    code: document.getElementById('cc_cvc'),
                    pmid: document.getElementById('update_pm_id'),
                    default_card_method: document.getElementById('default_card_method'),
                    ismanageScreen: document.getElementById('is_ebiz_manage_screen'),
                };
            }

},


    /**
     * @private
     */
   async _addNewEbizCard(ev) {
        document.getElementById('new_account_tab_id').value = "new_card";
    },

   /**
     * @private
     */
   async _addNewEbizAccount(ev) {
        document.getElementById('new_account_tab_id').value = "new_account";
    },


 /**
         * Return the credit card or bank data to pass to the Accept.dispatch request.
         *
         * @private
         * @param {number} acquirerId - The id of the selected acquirer
         * @return {Object} - Data to pass to the Accept.dispatch request
         */
async _getPaymentDetails (acquirerId) {
        const inputs = await  this._getInlineFormInputs(acquirerId);
        var tab = $('.nav-link.active');
        var acquirerType = document.getElementById('new_account_tab_id').value;
        var update_pm_id = document.getElementById('update_pm_id').value;
        var checkdefault = 'false'
        if (document.querySelector('.default_check:checked')){
            var checkdefault = 'true'
        }

        if (acquirerType === 'new_account'){
            var  validateInputsForm = $('input, select', '#addBankAccountDetails');
            var tokenBoxValsAch = 'false'
            if (document.querySelector('.token_box_ach_vals:checked')){
                var tokenBoxValsAch = 'true'

            }
            else if(inputs.tokenAccBox.value==='True') {
                var tokenBoxValsAch = 'true'
            }
             var  validateInputsForm = $('input, select', '#addBankAccountDetails');
            var wrong_input = false;
            validateInputsForm.toArray().forEach(function (element) {
                                //skip the check of non visible inputs
                                if ($(element).attr('type') == 'hidden') {
                                    return true;
                                }
                                $(element).closest('div.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
                                $(element).siblings( ".o_invalid_field" ).remove();
                                //force check of forms validity (useful for Firefox that refill forms automatically on f5)
                                $(element).trigger("focusout");
                                if (element.dataset.isRequired && element.value.length === 0) {
                                        $(element).closest('div.form-group').addClass('o_has_error').find('.form-control, .custom-select').addClass('is-invalid');
                                        var message = '<div style="color: red" class="o_invalid_field" aria-invalid="true">' + "The value is invalid." + '</div>';
                                        $(element).closest('div.form-group').append(message);
                                        wrong_input = true;
                                }
                                else if ($(element).closest('div.form-group').hasClass('o_has_error')) {
                                    wrong_input = true;
                                    if ($(element).closest('div.form-group').hasClass('account-number')) {
                                        var message = '<div style="color: red" class="o_invalid_field" aria-invalid="true">' + "Account number should be 4-17 digits." + '</div>';
                                    }
                                    else if ($(element).closest('div.form-group').hasClass('routing-number')) {
                                        var message = '<div style="color: red" class="o_invalid_field" aria-invalid="true">' + "Routing number should be 9 digits." + '</div>';
                                    }
                                    else if ($(element).closest('div.form-group').hasClass('card-number')) {
                                        $(element).closest('div.form-group').append('<div style="color: red" class="o_invalid_field" aria-invalid="true">' + "Card number should be valid and should be 13-19 digits." + '</div>');
                                    }
                                    else if ($(element).closest('div.form-group').hasClass('zip-code')) {
                                        $(element).closest('div.form-group').append('<div style="color: red" class="o_invalid_field" aria-invalid="true">' + "Zip/Postal Code can only include numbers, letters, and '-'. " + '</div>');
                                    }
                                    else{
                                        var message = '<div style="color: red" class="o_invalid_field" aria-invalid="true">' + "The value is invalid." + '</div>';
                                    }
                                    $(element).closest('div.form-group').addClass('o_has_error').find('.form-control, .custom-select').addClass('is-invalid');
                                    $(element).closest('div.form-group').append(message);
                                }
                            });
            if (wrong_input)
            {

               return;
            }
            else{
                return {
                acquirer_id: acquirerId,
                provider_id: acquirerId,
                update_pm_id: update_pm_id,
                is_manage_screen: inputs.ismanageScreen.value,
                bankData: {
                    nameOnAccount: inputs.accountName.value.substring(0, 22), // Max allowed by acceptjs
                    accountNumber: inputs.accountNumber.value,
                    routingNumber: inputs.routingNumber.value,
                    accountType: inputs.accountType.value,
                    tokenBox:   tokenBoxValsAch,
                    pmid: inputs.pmid.value,
                    default_card_method: checkdefault,
                },
            };
            }
        }
        else{
            var tokenBoxValsCredit = 'false'
            if (document.querySelector('.token_box_credit_vals:checked')){
                var tokenBoxValsCredit = 'true'
            }
            var  validateInputsForm = $('input, select', '#addCardDetails');
            var wrong_input = false;
            validateInputsForm.toArray().forEach(function (element) {
                                //skip the check of non visible inputs
                                if ($(element).attr('type') == 'hidden') {
                                    return true;
                                }
                                $(element).closest('div.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
                                $(element).siblings( ".o_invalid_field" ).remove();
                                //force check of forms validity (useful for Firefox that refill forms automatically on f5)
                                $(element).trigger("focusout");
                                if (element.dataset.isRequired && element.value.length === 0) {
                                        $(element).closest('div.form-group').addClass('o_has_error').find('.form-control, .custom-select').addClass('is-invalid');
                                        var message = '<div style="color: red" class="o_invalid_field" aria-invalid="true">' + "The value is invalid." + '</div>';
                                        $(element).closest('div.form-group').append(message);
                                        wrong_input = true;
                                }
                                else if ($(element).closest('div.form-group').hasClass('o_has_error')) {
                                    wrong_input = true;
                                    if ($(element).closest('div.form-group').hasClass('account-number')) {
                                        var message = '<div style="color: red" class="o_invalid_field" aria-invalid="true">' + "Account number should be 4-17 digits." + '</div>';
                                    }
                                    else if ($(element).closest('div.form-group').hasClass('routing-number')) {
                                        var message = '<div style="color: red" class="o_invalid_field" aria-invalid="true">' + "Routing number should be 9 digits." + '</div>';
                                    }
                                    else if ($(element).closest('div.form-group').hasClass('card-number')) {
                                        $(element).closest('div.form-group').append('<div style="color: red" class="o_invalid_field" aria-invalid="true">' + "Card number should be valid and should be 13-19 digits." + '</div>');
                                    }
                                    else if ($(element).closest('div.form-group').hasClass('zip-code')) {
                                        $(element).closest('div.form-group').append('<div style="color: red" class="o_invalid_field" aria-invalid="true">' + "Zip/Postal Code can only include numbers, letters, and '-'. " + '</div>');
                                    }
                                    else{
                                        var message = '<div style="color: red" class="o_invalid_field" aria-invalid="true">' + "The value is invalid." + '</div>';
                                    }
                                    $(element).closest('div.form-group').addClass('o_has_error').find('.form-control, .custom-select').addClass('is-invalid');
                                    $(element).closest('div.form-group').append(message);
                                }
                            });
            if (wrong_input)
            {
               return;
            }
            else{
            return {
                acquirer_id: acquirerId,
                provider_id: acquirerId,
                update_pm_id: update_pm_id,

                cardData: {
                    cardNumber: inputs.card.value.replace(/ /g, ''), // Remove all spaces
                    name: inputs.name.value,
                    street: inputs.street.value,
                    zip: inputs.zip.value,
                    expiry: inputs.expiry.value,
                    tokenBox:   tokenBoxValsCredit,
                    cardCode: inputs.code.value,
                    pmid: '',
                    default_card_method: checkdefault,
                },
            };
            }
        }
    },

    /**
     * @private
     */
   async _updateEbizAccount(ev) {
        document.getElementById('new_account_tab_id').value = "update_account";
        ev.stopPropagation();
        ev.preventDefault();
        $('input[data-provider="_updateEbizAccountebizcharge"][data-payment-option-type="acquirer"]').click()
        var currentEBIz = document.querySelectorAll('#ebiz_acquirer_select_auto');
        currentEBIz.forEach(function (element) {
            var dataValue = element.getAttribute("data-payment-option-name");
            if (dataValue=='EBizCharge'){
                    element.checked = true;
            }
        });
        $('.update_ebiz_account').prop('disabled', true);
        $('.delete_pm').prop('disabled', true);
        var pm_id = parseInt(ev.currentTarget.value);
        this.currently_updating = ev.target.parentElement;
        await this._getEbizToken(pm_id);

    },



        /**
     * @private
     */
   async _updateEbizCard(ev) {
        document.getElementById('new_account_tab_id').value = "update_card";
        ev.stopPropagation();
        ev.preventDefault();
        $('input[data-provider="ebizcharge"][data-payment-option-type="acquirer"]').click()
        var currentEBIz = document.querySelectorAll('#ebiz_acquirer_select_auto');
        currentEBIz.forEach(function (element) {
            var dataValue = element.getAttribute("data-payment-option-name");
            if (dataValue=='EBizCharge'){
                    element.checked = true;
            }
        });
        $('.update_ebiz_card').prop('disabled', true);
        $('.update_ebiz_card').css('color', '#bebebe');
        $('.update_ebiz_card').parent().css('cursor', 'default');
        $('.delete_pm').prop('disabled', true);
        $('.delete_pm').css('color', '#bebebe');
        var pm_id = parseInt(ev.currentTarget.value);
        this.currently_updating = ev.target.parentElement;
        await this._getEbizToken(pm_id);

    },


    /**
     * @private
     */

   async _getEbizToken(tokenId)  {
        this.rpc('/payment/ebizcharge/manage/token',{
              pm_id: tokenId}).then(ebizProcessingValues => {
                 console.log("in ebiz get token")
                 this._populateUpdateFields(ebizProcessingValues, tokenId)
            }).catch(error => {
                if (error instanceof RPCError) {
                    this._displayErrorDialog(_t("We are not able to delete your payment method at the moment."), error.data.message);
                    this._enableButton(); // The button has been disabled before initiating the flow.
                } else {
                    return Promise.reject(error);
                }
       });

    },

    /**
     * @private
     */
    async _populateUpdateFields(result, pm_id) {
        var acquirerType = document.getElementById('new_account_tab_id').value;
        var self = this
        if(result.token_type === 'credit'){
            document.getElementById('card-tab').click();
            var  validateInputsForm = $('input, select', '#addCardDetails');
            var achelement = document.getElementById("account-tab");
            achelement.classList.remove("active");
            var creditelement = document.getElementById("card-tab");
            creditelement.classList.add("active");
            self.$el.find("input[name='card_number']").attr('readonly',1)
            self.$el.find("input[name='card_number']").val(result.card_number)
            self.$el.find("input[name='account_holder_name']").val(result.account_holder_name)
            self.$el.find("input[name='avs_street']").val(result.avs_street)
            self.$el.find("input[name='avs_zip']").val(result.avs_zip)
            self.$el.find("input[name='card_expiration']").val(`${result.card_exp_month} / ${result.card_exp_year.slice(2,4)}`)
            self.$el.find("input[name='card_code']").val('')
            self.$el.find("input[name='partner_id']").val(result.partner_id[0])
            self.$el.find("input[name='update_pm_id']").val(pm_id)
            self.$el.find("input[name='default_card_method']").prop("checked", result.is_default)
            self.$el.find("input[name='default_card_method']").val(result.is_default)
            self.$el.find("input[name='card_type']").val(result.card_type)
        }
        else{
            document.getElementById('account-tab').click();
            var achelement = document.getElementById("account-tab");
            achelement.classList.add("active");
            var creditelement = document.getElementById("card-tab");
            creditelement.classList.remove("active");
            self.$el.find("input[name='bank_account_holder_name']").val(result.account_holder_name)
            self.$el.find("select").val(result.account_type)
            self.$el.find("input[name='bank_account_type']").attr('readonly',1)
            self.$el.find("input[name='account_number']").val(result.account_number)
            self.$el.find("input[name='account_number']").attr('readonly',1)
            self.$el.find("input[name='routing_number']").val(result.routing)
            self.$el.find("input[name='routing_number']").attr('readonly',1)
            self.$el.find("input[name='update_pm_id']").val(pm_id)
            self.$el.find("input[name='default_account_method']").prop("checked", result.is_default)
            self.$el.find("input[name='default_account_method']").val(result.is_default)
            self.$el.find("input[name='card_type']").val(result.card_type)

        }
//        $('button[name="o_payment_submit_button"]').html('<i class="fa fa-plus-circle"/> Update');
//        self.$el.find("#o_payment_cancel_button")[0].style = 'display: inline;'
    },

    /**
     * Display an error dialog.
     *
     * @private
     * @param {string} title - The title of the dialog.
     * @param {string} errorMessage - The error message.
     * @return {void}
     */
//    _displayErrorDialog(title, errorMessage = '') {
//        this.call('dialog', 'add', ConfirmationDialog, { title: title, body: errorMessage || "" });
//    },

    /**
     * Determine and return the id of the selected payment option.
     *
     * @private
     * @param {HTMLElement} radio - The radio button linked to the payment option.
     * @return {number} The id of the selected payment option.
     */
    _getPaymentOptionId(radio) {
        return Number(radio.dataset['paymentOptionId']);
    },


});
export default publicWidget.registry.eBizPaymentForm;
