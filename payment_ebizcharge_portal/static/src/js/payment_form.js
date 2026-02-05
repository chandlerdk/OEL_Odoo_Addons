/** @odoo-module **/
import { _t } from '@web/core/l10n/translation';
import { Component } from '@odoo/owl';
import { RPCError } from '@web/core/network/rpc_service';
import PaymentForm from '@payment/js/payment_form';

PaymentForm.include({
    events: Object.assign({}, PaymentForm.prototype.events || {}, {
        'change .surcharge_calc': '_onChangeCalcSurcharge',
        'click a.o_payment_provider_select_cc': '_onChangeCalcSurcharge',
        'click [name="new-account-tab"]': '_onChangeNewAccountValue',
        'click #new-card-tab': '_onChangeCardValue',
        'click #save-card-tab': '_onChangeSaveCardTab',
        'click #credit-tab': '_onClickCreditTab',
        'click #account-tab': '_onClickAccountTab',
    }),

    /**
     * @private
     */
    _onChangeCardValue: function(ev) {
        document.querySelectorAll("input[name=o_payment_radio]").forEach(function(element) {
            var dataValue = element.getAttribute("data-provider-code");
            var paymentMethod = element.getAttribute("data-payment-option-type");
            if (dataValue == 'ebizcharge' && paymentMethod == 'payment_method') {
                element.checked = true;
            }
        });
        initializeSavedCardTab();
        this._initializeSurchargeAmount();
    },

    _onChangeSaveCardTab: function(ev) {
        initializeNewCardTab();
        this._initializeSurchargeAmount();
    },

    /**
     * @private
     */
    _onChangeNewAccountValue: function(ev) {
        if (document.getElementById("new_account_tab_id_val")) {
            document.querySelectorAll("input[name=o_payment_radio]").forEach(function(element) {
                var dataValue = element.getAttribute("data-provider-code");
                var paymentMethod = element.getAttribute("data-payment-option-type");
                if (dataValue == 'ebizcharge' && paymentMethod == 'payment_method') {
                    element.checked = true;
                }
            });
            document.getElementById("new_account_tab_id_val").value = "new_account_val";
        }
    },

    /**
     * @private
     */
    _validateValuesBeforeSurchargeCheck: function(pm_vals) {
        var ccNumber = document.getElementById('cc_number').value;
        var avsZip = document.getElementById('avs_zip').value;
        var newCardTab = $('#new-card-tab').hasClass('active');
        if (pm_vals != '0' || (ccNumber != '' && avsZip != '' && newCardTab)) {
            return true;
        }
        return false;
    },

    _onClickCreditTab: function(ev) {
        initializeNewBankAccountTab();
        initializeSavedBankAccountTab();
        this._initializeSurchargeAmount();
    },

    _onClickAccountTab: function(ev) {
        initializeNewCardTab();
        initializeSavedCardTab();
        this._initializeSurchargeAmount();
    },

    _initializeSurchargeAmount() {
        const amountTotal = $('#total_order_amt_ebiz').val();
        $('#sur_amt_ebiz').text(parseFloat('0').toFixed(2));
        $('#sur_amt_ebiz_total').text(parseFloat(amountTotal).toFixed(2));
    },

    /**
     * @private
     */
    _onChangeCalcSurcharge: function(ev) {
        // Check radio button linked to selected payment option
        this._initializeSurchargeAmount();
        var checkedRadio = this.el.querySelector('input[name="o_payment_radio"]:checked');

        var allow_surcharge = 'false'
        // surcharge Amount calculation
        if (document.getElementById('allow_pay_surcharge')) {
            var allow_surcharge = document.getElementById('allow_pay_surcharge').value
        }
        var pm_vals = '0'
        if (checkedRadio && $('#save-card-tab').hasClass('active')) {
            var pm_vals = checkedRadio.value.split('-')[0]
            if (pm_vals == 'on') {
                var pm_vals = '0'
            }
        };
        if (allow_surcharge == 'True' && this._validateValuesBeforeSurchargeCheck(pm_vals)) {
            this._disableButton(true);
            var params = {
                'pm_id': pm_vals,
                'amount': document.getElementById('total_order_amt_ebiz').value,
                'cc_number': document.getElementById('cc_number').value,
                'avs_zip': document.getElementById('avs_zip').value,
            }
            this.rpc('/surcharge/check', {
                kwargs: params
            }).then(result => {
                const amountTotal = document.getElementById('total_order_amt_ebiz').value
                const totalInclSurcharge = parseFloat(amountTotal) + parseFloat(result.amount)
                document.getElementById('sur_amt_ebiz').textContent = result.amount.toFixed(2);
                document.getElementById('sur_amt_ebiz_total').textContent = totalInclSurcharge.toFixed(2);
                this._enableButton();
            });
        }
        //        }
    },

    /**
     * Open the inline form of the selected payment option, if any.
     *
     * @private
     * @param {Event} ev
     * @return {void}
     */
    async _selectPaymentOption(ev) {
        var checkedRadio = this.el.querySelector('input[name="o_payment_radio"]:checked');
        const providerCode = this.paymentContext.providerCode = this._getProviderCode(
            checkedRadio
        );
        if (providerCode !== 'ebizcharge') {
            return this._super(...arguments);
        }
        this._onChangeCalcSurcharge(ev)

        this._showHideSecurityInput(checkedRadio)
        await this._super(...arguments);
    },

    /**
     * show and hide the security code field on checkout form
     *
     * @private
     * @param {Event} checkedRadio
     * @return {void}
     */
    async _showHideSecurityInput(checkedRadio) {
        var pm_id = parseInt(checkedRadio.value.split('-')[0]);
        var token_type = checkedRadio.value.split('-')[1];
        if (token_type === 'ebizchargeCard') {
            document.getElementById(pm_id).style.display = 'block';
            document.getElementById('security-code-heading').style.display = 'block';
            document.getElementsByName("o_payment_radio").forEach(function(element) {
                if (!element.checked) {
                    if (element.value.includes('ebizchargeCard')) {
                        document.getElementById(parseInt(element.value.split('-')[0])).style.display = 'none';
                    }
                }
            });
        } else {
            if (document.getElementById('security-code-heading')) {
                document.getElementById('security-code-heading').style.display = 'none';
                document.getElementsByName("o_payment_radio").forEach(function(element) {
                    if (!element.checked) {
                        if (element.value.includes('ebizchargeCard')) {
                            document.getElementById(parseInt(element.value.split('-')[0])).style.display = 'none';
                        }
                    }
                })
            }
        }
    },

    /**
     * @private
     */
    _getAcquirerTypeFromCheckbox(element) {
        if (element) {
            if (element[0] == undefined) {
                return;
            } else {
                return element[0].id
            }
        } else {
            return;
        }
    },

    /**
     * @private
     * @param {jQuery} $form
     */
    async getFormData($form) {
        var unindexed_array = $form.serializeArray();
        var indexed_array = {};

        $.map(unindexed_array, function(n, i) {
            indexed_array[n.name] = n.value;
        });
        return indexed_array;
    },

    /**
     * Return all relevant inline form inputs based on the payment method type of the acquirer.
     *
     * @private
     * @param {number} acquirerId - The id of the selected acquirer
     * @return {Object} - An object mapping the name of inline form inputs to their DOM element
     */
    async _getInlineFormInputsEBiz(acquirerId) {
        var tab = $('.nav-link.active');
        var acquirerType = ''
        if (document.getElementById('new_account_tab_id_val')) {
            var acquirerType = document.getElementById('new_account_tab_id_val').value;
        }
        var authorizeOnly = this.$el.find("input[name='authorizeOnly']")[0]
        if (acquirerType === 'new_account_val') {
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
        } else {

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
     * Return the credit card or bank data to pass to the Accept.dispatch request.
     *
     * @private
     * @param {number} acquirerId - The id of the selected acquirer
     * @return {Object} - Data to pass to the Accept.dispatch request
     */
    async _getPaymentDetailsEBiz(acquirerId) {
        const inputs = await this._getInlineFormInputsEBiz(acquirerId);
        var tab = $('.nav-link.active');
        var acquirerType = ''
        if (document.getElementById('new_account_tab_id_val')) {
            var acquirerType = document.getElementById('new_account_tab_id_val').value;
        }
        var checkdefault = 'false'
        if (document.querySelector('.default_check:checked')) {
            var checkdefault = 'true'
        }
        if (acquirerType == 'new_account_val') {
            var tokenBoxValsAch = 'false'
            if (document.querySelector('.token_box_ach_vals:checked')) {
                var tokenBoxValsAch = 'true'
            } else if (inputs.tokenAccBox.value === 'True') {
                var tokenBoxValsAch = 'true'
            }
            return {
                acquirer_id: acquirerId,
                provider_id: acquirerId,
                bankData: {
                    nameOnAccount: inputs.accountName.value.substring(0, 22), // Max allowed by acceptjs
                    accountNumber: inputs.accountNumber.value,
                    routingNumber: inputs.routingNumber.value,
                    accountType: inputs.accountType.value,
                    tokenBox: tokenBoxValsAch,
                    pmid: inputs.pmid.value,
                    default_card_method: checkdefault,
                },
            };
        } else {
            var tokenBoxValsCredit = 'false'
            if (document.querySelector('.token_box_credit_vals:checked')) {
                var tokenBoxValsCredit = 'true'
            }
            return {
                acquirer_id: acquirerId,
                provider_id: acquirerId,

                cardData: {
                    cardNumber: inputs.card.value.replace(/ /g, ''), // Remove all spaces
                    name: inputs.name.value,
                    street: inputs.street.value,
                    zip: inputs.zip.value,
                    expiry: inputs.expiry.value,
                    tokenBox: tokenBoxValsCredit,
                    cardCode: inputs.code.value,
                    pmid: '',
                    default_card_method: checkdefault,
                },
            };
        }
    },

    /**
     * Prepare the inline form of Authorize.Net for direct payment.
     *
     * @override method from payment.payment_form_mixin
     * @private
     * @param {string} provider - The provider of the selected payment option's acquirer
     * @param {number} paymentOptionId - The id of the selected payment option
     * @param {string} flow - The online payment flow of the selected payment option
     * @return {Promise}
     */
    async _prepareInlineForm(providerId, providerCode, paymentOptionId, paymentMethodCode, flow) {
        if (providerCode !== 'ebizcharge') {
            return this._super(...arguments);
        }
        if (flow === 'token') {
            return Promise.resolve(); // Don't show the form for tokens
        }
        this._setPaymentFlow('direct');
    },

    /**
     * Prepare the params for the RPC to the transaction route.
     *
     * @private
     * @return {object} The transaction route params.
     */
    _prepareTransactionRouteParamsEbiz(tokenEbiz) {
        let transactionRouteParams = {
            'provider_id': this.paymentContext.providerId,
            'payment_method_id': this.paymentContext.paymentMethodId ?? null,
            'token_id': this.paymentContext.tokenId ?? null,
            'amount': this.paymentContext['amount'] !== undefined ?
                parseFloat(this.paymentContext['amount']) : null,
            'flow': this.paymentContext['flow'],
            'tokenization_requested': this.paymentContext['tokenizationRequested'],
            'landing_route': this.paymentContext['landingRoute'],
            'is_validation': this.paymentContext['mode'] === 'validation',
            'token_ebiz': tokenEbiz,
            'access_token': this.paymentContext['accessToken'],
            'csrf_token': odoo.csrf_token,
        };
        // Generic payment flows (i.e., that are not attached to a document) require extra params.
        if (this.paymentContext['transactionRoute'] === '/payment/transaction') {
            Object.assign(transactionRouteParams, {
                'currency_id': this.paymentContext['currencyId'] ?
                    parseInt(this.paymentContext['currencyId']) : null,
                'partner_id': parseInt(this.paymentContext['partnerId']),
                'reference_prefix': this.paymentContext['referencePrefix']?.toString(),
            });
        }
        return transactionRouteParams;
    },

    /**
     * Dispatch the secure data to EBizCharge.
     *
     * @override method from payment.payment_form_mixin
     * @private
     * @param {string} providerCode - The provider of the payment option's acquirer
     * @param {number} paymentOptionId - The id of the payment option handling the transaction
     * @param {string} paymentMethodCode - The payment method Code
     * @param {string} flow - The online payment flow of the transaction
     * @return {Promise}
     */
    async _submitForm(ev) {
        ev.stopPropagation();
        ev.preventDefault();
        // Block the entire UI to prevent fiddling with other widgets.
        this._disableButton(true);

        var checkedRadio = this.el.querySelector('input[name="o_payment_radio"]:checked');
        const providerCode = this._getProviderCode(
            checkedRadio
        );

        if (providerCode !== 'ebizcharge') {
            await this._super(...arguments); // Tokens are handled by the generic flow
            return;
        }

        var is_saved_card = false
        var is_saved_bank = false
        if (checkedRadio.value.includes('ebizchargeCard')) {
            is_saved_card = true
        }
        if (checkedRadio.value.includes('ebizchargeAccount')) {
            is_saved_bank = true
        }
        if (providerCode === 'ebizcharge' && is_saved_card == false && is_saved_bank == false) {
            var tab = $('.nav-link.active');
            var acquirerType = this._getAcquirerTypeFromCheckbox(tab);
            var tab_type = ''
            if (acquirerType == 'account-tab') {
                var validateInputsForm = $('input, select', '#addBankAccountDetails');
            } else if (acquirerType == 'credit-tab') {
                var validateInputsForm = $('input, select', '#addCardDetails');
            } else {
                this._enableButton();
                this._displayErrorDialog(
                    _t("Payment processing failed"),
                    _t("Configuration required. Please add a valid website to the EBizCharge Merchant Account or select a merchant account on the customer profile.")
                );
                return;
            }


            var wrong_input = false;
            validateInputsForm.toArray().forEach(function(element) {
                //skip the check of non visible inputs
                if ($(element).attr('type') == 'hidden') {
                    return true;
                }
                $(element).closest('div.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
                $(element).siblings(".o_invalid_field").remove();
                //force check of forms validity (useful for Firefox that refill forms automatically on f5)
                $(element).trigger("focusout");
                if (element.dataset.isRequired && element.value.length === 0) {
                    $(element).closest('div.form-group').addClass('o_has_error').find('.form-control, .custom-select').addClass('is-invalid');
                    var message = '<div style="color: red" class="o_invalid_field" aria-invalid="true">' + "The value is invalid." + '</div>';
                    $(element).closest('div.form-group').append(message);
                    wrong_input = true;
                } else if ($(element).closest('div.form-group').hasClass('o_has_error')) {
                    wrong_input = true;
                    if ($(element).closest('div.form-group').hasClass('account-number')) {
                        var message = '<div style="color: red" class="o_invalid_field" aria-invalid="true">' + "Account number should be 4-17 digits." + '</div>';
                    } else if ($(element).closest('div.form-group').hasClass('routing-number')) {
                        var message = '<div style="color: red" class="o_invalid_field" aria-invalid="true">' + "Routing number should be 9 digits." + '</div>';
                    } else if ($(element).closest('div.form-group').hasClass('card-number')) {
                        $(element).closest('div.form-group').append('<div style="color: red" class="o_invalid_field" aria-invalid="true">' + "Card number should be valid and should be 13-19 digits." + '</div>');
                    } else if ($(element).closest('div.form-group').hasClass('zip-code')) {
                        $(element).closest('div.form-group').append('<div style="color: red" class="o_invalid_field" aria-invalid="true">' + "Zip/Postal Code can only include numbers, letters, and '-'. " + '</div>');
                    } else {
                        var message = '<div style="color: red" class="o_invalid_field" aria-invalid="true">' + "The value is invalid." + '</div>';
                    }
                    $(element).closest('div.form-group').addClass('o_has_error').find('.form-control, .custom-select').addClass('is-invalid');
                    $(element).closest('div.form-group').append(message);
                }
            });
            if (wrong_input) {
                this._enableButton();
                return;
            }

        }

        if (providerCode === 'ebizcharge' && is_saved_card == false && is_saved_bank == false) {
            var tokenEbiz = await this._getPaymentDetailsEBiz(this._getProviderId(checkedRadio))
            if (ev.currentTarget.form) {
                const partnerId = ev.currentTarget.form.dataset.partnerId;
                if (partnerId) {
                    tokenEbiz.partner_id = parseInt(ev.currentTarget.form.dataset.partnerId);
                }
            }
            this.rpc('/payment/ebizcharge/s2s/create_json_3ds', {
                kwargs: tokenEbiz
            }).then(ebizProcessingValues => {
                if (ebizProcessingValues) {
                    this.paymentContext.tokenId = ebizProcessingValues.id;
                    //                        this.paymentContext.flow="redirect"
                    this.paymentContext.providerId = this._getProviderId(checkedRadio);
                    const flow = this.paymentContext.flow = this._getPaymentFlow(checkedRadio);
                    const paymentOptionId = this.paymentContext.paymentOptionId = this._getPaymentOptionId(
                        checkedRadio
                    );
                    const inlineForm = this._getInlineForm(checkedRadio);
                    this.paymentContext.tokenizationRequested = inlineForm?.querySelector(
                        '[name="o_payment_tokenize_checkbox"]'
                    )?.checked ?? this.paymentContext['mode'] === 'validation';
                    this.paymentContext.paymentMethodId = paymentOptionId;
                    const providerCode = this.paymentContext.providerCode = this._getProviderCode(
                        checkedRadio
                    );
                    const pmCode = this.paymentContext.paymentMethodCode = this._getPaymentMethodCode(
                        checkedRadio
                    );
                    this.rpc(
                        this.paymentContext['transactionRoute'],
                        this._prepareTransactionRouteParamsEbiz(tokenEbiz),
                    ).then(processingValues => {
                        const flow = 'token'
                        if (flow === 'redirect') {
                            this._processRedirectFlow(
                                providerCode, paymentOptionId, pmCode, processingValues
                            );
                        } else if (flow === 'direct') {
                            this._processDirectFlow(
                                providerCode, paymentOptionId, pmCode, processingValues
                            );

                        } else if (flow === 'token') {
                            this._processTokenFlow(
                                providerCode, paymentOptionId, pmCode, processingValues
                            );
                        }
                    }).catch(error => {
                        if (error instanceof RPCError) {
                            this._enableButton();
                            this._displayErrorDialog(_t("Payment processing failed"), error.data.message);
                        } else {
                            return Promise.reject(error);
                        }
                    });
                }
            }).catch(error => {
                if (error instanceof RPCError) {
                    this._enableButton();
                    this._displayErrorDialog(_t("Payment processing failed"), error.data.message);
                } else {
                    return Promise.reject(error);
                }
            });
        }
        // Make the payment

        // new alert

        if (checkedRadio.value.includes('ebizchargeCard') && is_saved_card == true) {
            var form = this.el;
            var isValue = false

            for (let i = 1; i < form.elements.length; i++) {
                if (checkedRadio.value.split('-')[0] === form[i].id) {
                    if (form[i].name === 'security-code') {
                        if (form[i].value != "" && form[i].value.length >= 3) {
                            isValue = true;
                        }
                    }
                }
            }
            if (isValue === false) {
                this._enableButton();
                this._displayErrorDialog(
                    _t("Payment processing failed"),
                    _t("Please enter valid security code while paying with saved cards.")
                );
                return;
            } else {
                return await this._super(...arguments);

            }

        } else if (checkedRadio.value.includes('ebizchargeAccount') && is_saved_bank == true) {
            return await this._super(...arguments);
        }
    },

    /**
     * Checks that all payment inputs adhere to the DOM validation constraints.
     *
     * @private
     * @param {number} acquirerId - The id of the selected acquirer
     * @return {boolean} - Whether all elements pass the validation constraints
     */
    async _validateFormInputs(acquirerId) {
        if (this._getInlineFormInputsEBiz(acquirerId)) {
            const inputs = Object.values(this._getInlineFormInputsEBiz(acquirerId));
            return inputs.every(element => element.reportValidity());
        }
    },

});

function setCardNumberToBlank() {
    $('#cc_number').val('');
    $('#cc_number').parent('.form-group').removeClass('o_has_success').find('.form-control, .custom-select').removeClass('is-valid');
    $('#cc_number').parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
    $('#cc_number').siblings('.o_invalid_field').remove();
}

function setCardHolderNameToBlank() {
    $('#cc_holder_name').val('');
    $('#cc_holder_name').parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
    $('#cc_holder_name').siblings('.o_invalid_field').remove();
}

function setAvsStreetToBlank() {
    $('#avs_street').val('');
    $('#avs_street').parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
    $('#avs_street').siblings('.o_invalid_field').remove();
}

function setAvsZipToBlank() {
    $('#avs_zip').val('');
    $('#avs_zip').parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
    $('#avs_zip').parent('.form-group').removeClass('o_has_success').find('.form-control, .custom-select').removeClass('is-valid');
}

function setCCExpiryToBlank() {
    $('#cc_expiry').val('');
    $('#cc_expiry').parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
    $('#cc_expiry').parent('.form-group').removeClass('o_has_success').find('.form-control, .custom-select').removeClass('is-valid');
}

function setCCCVCToBlank() {
    $('#cc_cvc').val('');
    $('#cc_cvc').parent('.form-group').removeClass('o_has_success').find('.form-control, .custom-select').removeClass('is-valid');
    $('#cc_cvc').parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
    $('#cc_cvc').siblings('.o_invalid_field').remove();
}

function initializeNewCardTab() {
    setCardNumberToBlank();
    setCardHolderNameToBlank();
    setAvsStreetToBlank();
    setAvsZipToBlank();
    setCCExpiryToBlank();
    setCCCVCToBlank();

    $('#token_save_box_credit').prop('checked', false);
    $('#default_card_method').prop('checked', false);
}

function initializeSavedCardTab() {
    var checkedRadio = $('input[name="o_payment_radio"]');
    if (checkedRadio && $('#save-card-tab').hasClass('active')) {
        checkedRadio.prop('checked', false).change();
    }
    $('input[name="security-code"]').removeClass('is-valid').parent('.form-group').removeClass('o_has_success');
    $('input[name="security-code"]').removeClass('is-invalid').parent('.form-group').removeClass('o_has_error');
    $('input[name="security-code"]').siblings('.o_invalid_field').remove();
    $('input[name="security-code"]').val('');
}

function setAccountHolderNameToBlank() {
    $('#bank_account_holder_name').val('');
    $('#bank_account_holder_name').parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
    $('#bank_account_holder_name').siblings('.o_invalid_field').remove();
}

function setAccountNumberToBlank() {
    $('#account_number').val('');
    $('#account_number').parent('.form-group').removeClass('o_has_success').find('.form-control, .custom-select').removeClass('is-valid');
    $('#account_number').parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
    $('#account_number').siblings('.o_invalid_field').remove();
}

function setRoutingNumberToBlank() {
    $('#routing_number').val('');
    $('#routing_number').parent('.form-group').removeClass('o_has_success').find('.form-control, .custom-select').removeClass('is-valid');
    $('#routing_number').parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
    $('#routing_number').siblings('.o_invalid_field').remove();
}

function initializeNewBankAccountTab() {
    setAccountHolderNameToBlank();
    setAccountNumberToBlank();
    setRoutingNumberToBlank();

    $('#bank_account_type').val('Checking');

    $('#token_save_box_acc').prop('checked', false);
    $('#default_account_method').prop('checked', false);
}

function initializeSavedBankAccountTab() {
    var checkedRadio = $('input[name="o_payment_radio"]');
    if (checkedRadio && $('#save-account-tab').hasClass('active')) {
        checkedRadio.prop('checked', false).change();
    }
}