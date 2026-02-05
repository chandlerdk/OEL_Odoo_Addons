odoo.define('payment_ebizcharge_portal.manage_form', function (require) {
"use strict";
var manageForm = require('payment.manage_form');
var rpc = require('web.rpc');

manageForm.include({
    events: _.extend({},{
        'click button[name="update_pm"]': '_updatePmEvent',
        'click button[name="delete_ebiz_pm"]': '_deletePmEvent',
        'click #o_payment_cancel_button': '_cancelButton',
        'click .nav-link': '_showHideDetails',
        'click button[name="refresh_payment_tokens"]': 'refreshPaymentMethods',
    }, manageForm.prototype.events),

    /**
     * @private
     */
    getAcquirerIdFromRadio: function (element) {
        return $(element).data('payment-option-id');
    },

     /**
     * @private
     */
    _deletePmEvent: function (ev) {
         if (confirm('Are you sure you want to delete this record?')){
        ev.stopPropagation();
        ev.preventDefault();
        $('input[data-provider="ebizcharge"][data-payment-option-type="acquirer"]').click()
        $('.update_pm').prop('disabled', true);
        $('.delete_pm').prop('disabled', true);
        var self = this;
        var pm_id = parseInt(ev.currentTarget.value);
        self.currently_updating = ev.target.parentElement
        self._rpc({
            model: 'payment.token',
            method: 'token_action_archive',
            args: [pm_id],
        }).then(function (result) {
            if (result === true) {
                ev.target.closest('tr').remove();
            }
            window.location ='/my/ebiz_payment_method'
        }, function () {
            self.displayError(
                _t('Server Error'),
                _t("We are not able to delete your payment method at the moment.")
            );
        });
       }
       else{
            return false
        }
    },

    /**
     * @private
     */
    _updatePmEvent: function (ev) {
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
        $('.update_pm').prop('disabled', true);
        $('.delete_pm').prop('disabled', true);
        var self = this;
        var pm_id = parseInt(ev.currentTarget.value);
        self.currently_updating = ev.target.parentElement
        self._rpc({
            route: '/payment/ebizcharge/get/token',
            params: { 'pm_id': pm_id},
        }).then(function (result) {
            self.populateUpdateFields(result, pm_id)
        }, function () {
            self.displayError(
                _t('Server Error'),
                _t("We are not able to delete your payment method at the moment.")
            );
        });

    },

    /**
     * @private
     */
    populateUpdateFields: function (result, pm_id) {
        var self = this
        if(result.token_type === 'credit'){
//            var achelement = document.getElementById("account-tab");
//            achelement.classList.remove("active");
//            var creditelement = document.getElementById("card-tab");
//            creditelement.classList.add("active");
            self.$el.find("input[name='card_number']").attr('readonly',1)
            self.$el.find("input[name='card_number']").val(result.card_number)
            self.$el.find("input[name='account_holder_name']").val(result.account_holder_name)
            self.$el.find("input[name='avs_street']").val(result.avs_street)
            self.$el.find("input[name='avs_zip']").val(result.avs_zip)
            self.$el.find("input[name='card_expiration']").val(`${result.card_exp_month} / ${result.card_exp_year.slice(2,4)}`)
            self.$el.find("input[name='card_code']").val("")
            self.$el.find("input[name='partner_id']").val(result.partner_id[0])
            self.$el.find("input[name='update_pm_id']").val(pm_id)
            self.$el.find("input[name='default_card_method']").prop("checked", result.is_default)
            self.$el.find("input[name='default_card_method']").val(result.is_default)
            self.$el.find("input[name='card_type']").val(result.card_type)
        }
        else{
//            var creditelement = document.getElementById("card-tab");
//            creditelement.classList.remove("active");
//            var achelement = document.getElementById("account-tab");
//            achelement.classList.add("active");
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
        $('button[name="o_payment_submit_button"]').html('<i class="fa fa-plus-circle"/> Update');
        self.$el.find("#o_payment_cancel_button")[0].style = 'display: inline;'
    },

    /**
     * @private
     */
    _cancelButton: function (ev) {
        ev.stopPropagation();
        ev.preventDefault();
        $('.update_pm').prop('disabled', false);
        $('.delete_pm').prop('disabled', false);
        var checked_radio = this.$('input[type="radio"]:checked');
        var acquirer_id = this.getAcquirerIdFromRadio(checked_radio);
        var acquirer_form = this.$('#o_payment_provider_inline_form_' + acquirer_id);
        var inputs_form = $('input', acquirer_form);
        for (var i=0; i<inputs_form.length; i++) {
            if (inputs_form[i].id){
                if (inputs_form[i].id != 'addCardBank1' && inputs_form[i].id != 'addCardBank2'){
                    inputs_form[i].value = ''
                    inputs_form[i].removeAttribute('readonly')
                }
            }
        }

        var tab = $('.nav-link.active');
        if(tab[0].id === 'card-tab'){
            $('button[name="o_payment_submit_button"]').html('<i class="fa fa-plus-circle"/> Add new card');
            this.$el.find("input[name='default_card_method']").prop("checked", false);
            this.$el.find("input[name='default_card_method']").val(false);
        }
        else{
            $('button[name="o_payment_submit_button"]').html('<i class="fa fa-plus-circle"/> Add new account');
            this.$el.find("input[name='default_account_method']").prop("checked", false);
            this.$el.find("input[name='default_account_method']").val(false);
        }
        this.$el.find("#o_payment_cancel_button")[0].style = 'display: none;'
    },

    /**
     * @private
     */
    _showHideDetails: function(ev){
        var updateOrNot = $('input[name="update_pm_id"]').val()
        if(updateOrNot === ""){
            var $checkedRadio = this.$('input[type="radio"]:checked');
            if(ev.currentTarget.id==='account-tab' && $checkedRadio.data('provider') === 'ebizcharge'){
                $('button[name="o_payment_submit_button"]').html('<i class="fa fa-plus-circle"/> Add new account');
            }
            else{
                $('button[name="o_payment_submit_button"]').html('<i class="fa fa-plus-circle"></i> Add new card');
            }
        }

    },

    /**
     * @override
     */
    _onClickPaymentOption: function (ev) {
        this._super.apply(this, arguments);
        var $checkedRadio = this.$('input[type="radio"]:checked');
        var tab = $('.nav-link.active');

        if($checkedRadio.data('provider') === 'ebizcharge' && $checkedRadio.data('value')===' '){
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
//                Dialog.alert(self, _t("Payment methods are up to date!"), {
//                        title: _t('Success'),
//                    }).then(function (){
//                        self.trigger_up('reload');
//                    });
                alert("Payment methods are up to date!")
                location.reload()
            });
        },

});
});
