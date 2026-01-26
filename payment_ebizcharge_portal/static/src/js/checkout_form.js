odoo.define('payment_ebizcharge_portal.checkout_form', function (require) {
"use strict";
var checkoutForm = require('payment.checkout_form');
var rpc = require('web.rpc');    

checkoutForm.include({
    events: _.extend({},{
        'click .o_payment_provider_select': '_onClickPaymentOption',
        'click button[name="refresh_payment_tokens"]': 'refreshPaymentMethods',
        'change .surcharge_calc':  '_onChangeCalcSurcharge',
    }, checkoutForm.prototype.events),

    /**
     * @private
     */
    _onChangeCalcSurcharge: function (ev) {
        // Check radio button linked to selected payment option
        const checkedRadio = $(ev.currentTarget).find('input[name="o_payment_radio"]')[0];
        $(checkedRadio).prop('checked', true);
        var allow_surcharge = 'false'
        // surcharge Amount calculation
        if (document.getElementById('allow_pay_surcharge')){
             var allow_surcharge = document.getElementById('allow_pay_surcharge').value
        }

        if (allow_surcharge=='True'){
//            if (checkedRadio || document.getElementById('cc_number').value !='' && document.getElementById('avs_zip').value !='') {
                rpc.query({
                        route: '/surcharge/check',
                        params: {
                        'pm_id': $(checkedRadio).data('payment-option-id'),
                        'amount': document.getElementById('total_order_amt_ebiz').value,
                        'cc_number': document.getElementById('cc_number').value,
                        'avs_zip': document.getElementById('avs_zip').value,
                        },
                }).then(function (result) {
                     const amountTotal = document.getElementById('total_order_amt_ebiz').value
                     const totalInclSurcharge = parseFloat(amountTotal) + parseFloat(result.amount)
                     document.getElementById('sur_amt_ebiz').textContent=result.amount.toFixed(2);
                     document.getElementById('sur_amt_ebiz_total').textContent = totalInclSurcharge.toFixed(2);

                });
            }
//        }
     },

    _showHideSecurityInput: function (checkedRadio) {
        var pm_id = parseInt(checkedRadio.value.split('-')[0]);
        var token_type = checkedRadio.value.split('-')[1];
        if(token_type === 'ebizchargeCard'){
            document.getElementById(pm_id).style.display = 'block';
            document.getElementById('security-code-heading').style.display = 'block';
            document.getElementsByName("o_payment_radio").forEach(function (element) {
                if (!element.checked){
                    if(element.value.includes('ebizchargeCard')){
                        document.getElementById(parseInt(element.value.split('-')[0])).style.display = 'none';
                    }
                }
            });
        }
        else{
            if(document.getElementById('security-code-heading')){
                document.getElementById('security-code-heading').style.display = 'none';
                document.getElementsByName("o_payment_radio").forEach(function (element) {
                    if (!element.checked){
                        if(element.value.includes('ebizchargeCard')){
                            document.getElementById(parseInt(element.value.split('-')[0])).style.display = 'none';
                        }
                    }
                })
            }
        }
    },

    _onClickPaymentOption: function (ev) {
        // Uncheck all radio buttons
        this.$('input[name="o_payment_radio"]').prop('checked', false);
        var currentEBIz = document.querySelectorAll('#ebiz_acquirer_select_auto');
        currentEBIz.forEach(function (element) {
            var dataValue = element.getAttribute("data-payment-option-name");
            if (dataValue=='EBizCharge'){
                    element.checked = true;
            }
        });

        // Check radio button linked to selected payment option
        const checkedRadio = $(ev.currentTarget).find('input[name="o_payment_radio"]')[0];
        $(checkedRadio).prop('checked', true);
//        if (checkedRadio){
            // surcharge Amount calculation
        this._onChangeCalcSurcharge(ev)
//        }
        if ($(checkedRadio).data('provider') === 'ebizcharge'){
            this._showHideSecurityInput(checkedRadio)
        }
        // Show the inputs in case they had been hidden
        this._showInputs();

        // Disable the submit button while building the content
        this._disableButton(false);

        // Unfold and prepare the inline form of selected payment option
        this._displayInlineForm(checkedRadio);

        // Re-enable the submit button
        this._enableButton();

        var $checkedRadio = this.$('input[type="radio"]:checked');
        var tab = $('.nav-link.active');
        if($checkedRadio.data('provider') === 'ebizcharge' && $checkedRadio.data('value')==' '){
            if(tab[0].id==='account-tab'){
                $('button[name="o_payment_submit_button"]').html('<i class="fa fa-plus-circle"/> Add new account');
            }
            if(tab[0].id==='card-tab'){
                $('button[name="o_payment_submit_button"]').html('<i class="fa fa-plus-circle"></i> Add new card');
            }
        }
},

    refreshPaymentMethods: function (ev) {
            var self = this;
            rpc.query({
                route: '/refresh_payment_profiles',
                params: { 'pm_id': '1'},
            }).then(function (result) {

                alert("Payment methods are up to date!")
                location.reload()
            });
        },

});
});
