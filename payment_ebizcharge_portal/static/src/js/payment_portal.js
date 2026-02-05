$(function () {

    var updateOrNot = $('input[name="update_pm_id"]').val()
    if(updateOrNot === ""){
        $('input#cc_number').payment('formatCardNumber');
        $('input#account_number').payment('formatAccountNumber');
        $('input#routing_number').payment('formatRoutingNumber');
    }
    $('input#cc_cvc').payment('formatCardCVC');
    $('input#cc_expiry').payment('formatCardExpiry');
    $('input[name="security-code"]').payment('formatSecurityCode');

    $('input#cc_number').on('focusout', function (e) {
        var updateOrNot = $('input[name="update_pm_id"]').val()
        if(updateOrNot === ""){
            var valid_value = $.payment.validateCardNumber(this.value);

            var card_type = $.payment.cardType(this.value);
            if (card_type) {
                $(this).parent('.form-group').children('.card_placeholder').removeClass().addClass('card_placeholder ' + card_type);
                $(this).parent('.form-group').children('input[name="cc_brand"]').val(card_type)
            }
            else {
                $(this).parent('.form-group').children('.card_placeholder').removeClass().addClass('card_placeholder');
            }
            if (valid_value) {
                $(this).parent('.form-group').addClass('o_has_success').find('.form-control, .custom-select').addClass('is-valid');
                $(this).parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
                $(this).siblings('.o_invalid_field').remove();
            }
            else {
                $(this).parent('.form-group').addClass('o_has_error').find('.form-control, .custom-select').addClass('is-invalid');
                $(this).parent('.form-group').removeClass('o_has_success').find('.form-control, .custom-select').removeClass('is-valid');
            }
        }
        else{
            $(this).parent('.form-group').addClass('o_has_success').find('.form-control, .custom-select').addClass('is-valid');
            $(this).parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
            $(this).siblings('.o_invalid_field').remove();
        }
    });

    $('input#cc_cvc').on('focusout', function (e) {
        var updateOrNot = $('input[name="update_pm_id"]').val();
        if(updateOrNot === ""){
            var cc_nbr = $(this).parents('#addCardDetails').find('#cc_number').val();
            var card_type = $.payment.cardType(cc_nbr);
            var valid_value = $.payment.validateCardCVC(this.value, card_type);

            if (valid_value) {
                $(this).parent('.form-group').addClass('o_has_success').find('.form-control, .custom-select').addClass('is-valid');
                $(this).parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
                $(this).siblings('.o_invalid_field').remove();
            }
            else if ($('#verify_credit_card_before_saving').val() != 'true'){
                 $(this).parent('.form-group').removeClass('o_has_success').find('.form-control, .custom-select').removeClass('is-valid');
                 $(this).parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
                 $(this).siblings('.o_invalid_field').remove();
             }
            else {
                $(this).parent('.form-group').addClass('o_has_error').find('.form-control, .custom-select').addClass('is-invalid');
                $(this).parent('.form-group').removeClass('o_has_success').find('.form-control, .custom-select').removeClass('is-valid');
            }
        }
        else{
            var odoo_card_type = $('input[name="card_type"]').val();
            var card_type = getCardType(odoo_card_type);
            var valid_value = $.payment.validateCardCVC(this.value, card_type);
            if (valid_value) {
                $(this).parent('.form-group').addClass('o_has_success').find('.form-control, .custom-select').addClass('is-valid');
                $(this).parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
                $(this).siblings('.o_invalid_field').remove();
            }
            else if ($('#verify_credit_card_before_saving').val() != 'true'){
                 $(this).parent('.form-group').removeClass('o_has_success').find('.form-control, .custom-select').removeClass('is-valid');
                 $(this).parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
                 $(this).siblings('.o_invalid_field').remove();
             }
            else {
                $(this).parent('.form-group').addClass('o_has_error').find('.form-control, .custom-select').addClass('is-invalid');
                $(this).parent('.form-group').removeClass('o_has_success').find('.form-control, .custom-select').removeClass('is-valid');
            }
        }
    });

    $('input#cc_holder_name').on('focusout', function (e) {
        $(this).parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
        $(this).siblings('.o_invalid_field').remove();

    });

    $('input#avs_street').on('focusout', function (e) {
        $(this).parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
        $(this).siblings('.o_invalid_field').remove();

    });

    $('input#avs_zip').on('focusout', function (e) {
        var valid_value = validateZipCode(this.value);

        if (valid_value) {
            $(this).parent('.form-group').addClass('o_has_success').find('.form-control, .custom-select').addClass('is-valid');
            $(this).parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
            $(this).siblings('.o_invalid_field').remove();
        }
        else {
            $(this).parent('.form-group').addClass('o_has_error').find('.form-control, .custom-select').addClass('is-invalid');
            $(this).parent('.form-group').removeClass('o_has_success').find('.form-control, .custom-select').removeClass('is-valid');
        }
    });

    $('input#cc_expiry').on('focusout', function (e) {
        var expiry_value = $.payment.cardExpiryVal(this.value);
        var month = expiry_value.month || '';
        var year = expiry_value.year || '';
        var valid_value = $.payment.validateCardExpiry(month, year);

        if (valid_value) {
            $(this).parent('.form-group').addClass('o_has_success').find('.form-control, .custom-select').addClass('is-valid');
            $(this).parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
            $(this).siblings('.o_invalid_field').remove();
        }
        else {
            $(this).parent('.form-group').addClass('o_has_error').find('.form-control, .custom-select').addClass('is-invalid');
            $(this).parent('.form-group').removeClass('o_has_success').find('.form-control, .custom-select').removeClass('is-valid');
        }
    });

    $('input#bank_account_holder_name').on('focusout', function (e) {
        $(this).parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
        $(this).siblings('.o_invalid_field').remove();

    });

    $('input#account_number').on('focusout', function (e) {
        var updateOrNot = $('input[name="update_pm_id"]').val()
        if(updateOrNot === ""){
            var valid_value = $.payment.validateAccountNumber(this.value);

            if (valid_value) {
                $(this).parent('.form-group').addClass('o_has_success').find('.form-control, .custom-select').addClass('is-valid');
                $(this).parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
                $(this).siblings('.o_invalid_field').remove();
            }
            else {
                $(this).parent('.form-group').addClass('o_has_error').find('.form-control, .custom-select').addClass('is-invalid');
                $(this).parent('.form-group').removeClass('o_has_success').find('.form-control, .custom-select').removeClass('is-valid');
            }
        }
        else{
            $(this).parent('.form-group').addClass('o_has_success').find('.form-control, .custom-select').addClass('is-valid');
            $(this).parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
            $(this).siblings('.o_invalid_field').remove();
        }

    });

    $('input#routing_number').on('focusout', function (e) {
        var updateOrNot = $('input[name="update_pm_id"]').val()
        if(updateOrNot === ""){
            var valid_value = $.payment.validateRoutingNumber(this.value);

            if (valid_value) {
                $(this).parent('.form-group').addClass('o_has_success').find('.form-control, .custom-select').addClass('is-valid');
                $(this).parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
                $(this).siblings('.o_invalid_field').remove();
            }
            else {
                $(this).parent('.form-group').addClass('o_has_error').find('.form-control, .custom-select').addClass('is-invalid');
                $(this).parent('.form-group').removeClass('o_has_success').find('.form-control, .custom-select').removeClass('is-valid');
            }
        }
        else {
            $(this).parent('.form-group').addClass('o_has_success').find('.form-control, .custom-select').addClass('is-valid');
            $(this).parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
            $(this).siblings('.o_invalid_field').remove();
        }

    });

    $('select[name="pm_acquirer_id"]').on('change', function() {
        var acquirer_id = $(this).val();
        $('.acquirer').addClass('d-none');
        $('.acquirer[data-acquirer-id="'+acquirer_id+'"]').removeClass('d-none');
    });

    $('input[name="security-code"]').on('focusout', function (e) {
        var valid_value = $.payment.validateSecurityCode(this.value);

        if (valid_value) {
            $(this).parent('.form-group').addClass('o_has_success').find('.form-control, .custom-select').addClass('is-valid');
            $(this).parent('.form-group').removeClass('o_has_error').find('.form-control, .custom-select').removeClass('is-invalid');
            $(this).siblings('.o_invalid_field').remove();
        }
        else {
            $(this).parent('.form-group').addClass('o_has_error').find('.form-control, .custom-select').addClass('is-invalid');
            $(this).parent('.form-group').removeClass('o_has_success').find('.form-control, .custom-select').removeClass('is-valid');
        }

    });

});
function validateZipCode(value){
    newValueArray = value.split('-');
    return newValueArray.every(matchRegex);
}
function matchRegex(element){
    var regEx = /^[0-9a-zA-Z-]+$/;
    return element.match(regEx);
}


function getCardType(type){
    switch (type) {
        case 'M':
            return 'mastercard';
            break;
        case 'V':
            return 'visa';
            break;
        case 'A':
            return 'amex';
            break;
        case 'DS':
            return 'discover';
            break;
        case 'J':
            return 'jcb';
    }
}

function myFunction() {
  var x = document.getElementById("CreditCards");
  var y = document.getElementById("BankAccounts");
  var a = document.getElementById("CreditCardsDetails");
  var b = document.getElementById("BankAccountsDetails");

  if (x){
    if (x.style.display === "none") {
        x.style.display = "block";
    } else {
        x.style.display = "none";
    }
  }
  if(y){
    if (y.style.display === "none") {
        y.style.display = "block";
    } else {
        y.style.display = "none";
    }
  }

  if(a){
    if (a.style.display === "none") {
        a.style.display = "block";
    } else {
        a.style.display = "none";
    }
  }
  if(b){
    if (b.style.display === "none") {
        b.style.display = "block";
    } else {
        b.style.display = "none";
    }
  }

}
