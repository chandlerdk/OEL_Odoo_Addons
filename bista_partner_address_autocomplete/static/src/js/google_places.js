/** @odoo-module **/

import {registry} from "@web/core/registry";
import {CharField, charField} from "@web/views/fields/char/char_field";
import {onMounted, onWillUpdateProps, Component} from "@odoo/owl";
import { session } from "@web/session";

export class AddressAutoComplete extends CharField {
    setup() {
        super.setup();

        onWillUpdateProps(async (nextProps) => {
            if (this.element && nextProps.value && nextProps.value.length) {
                this.initAutocomplete()
            }
        })


        onMounted(async () => {
            this.element = $(this.__owl__.bdom.parentEl)
            this.parent = this.element.parent()
            this.initAutocomplete()
        })

    }


    async initAutocomplete() {
        if (!this.props || !this.props.record) {
            return
        }


        this.street_address = document.getElementById("street_0")

        if (this.street_address && !this.street_address.getAttribute("subscribed")) {
            this.city_input = document.getElementById("city_0")
            this.country_input = document.getElementById("country_id_0")
            this.state_input = document.getElementById("state_id_0")
            this.zip_input = document.getElementById("zip_0")



            let company_id = session.user_companies.current_company
            let company = await this.env.model.orm.read('res.company', [company_id], ['autocomplete_addresses','google_places_api'])

            if (!company.length) {
                console.log("GOOGLE PLACES API: Something is wrong with the company or user session.")
                return
            }

            let {autocomplete_addresses, google_places_api} = company[0]

            if(!autocomplete_addresses || !google_places_api) {
                console.log("Company ", autocomplete_addresses, google_places_api)
                return
            }

            var loader = new google.maps.plugins.loader.Loader({
                apiKey: google_places_api,
                version: "weekly",
                libraries: ["places"]
            });
            let google_api = await loader.load()

             this.autocomplete = new google_api.maps.places.Autocomplete(this.street_address, {
                componentRestrictions: {country: ["us", "ca"]},
                fields: ["address_components", "geometry"],
                types: ["address"],
            });

            this.autocomplete.addListener("place_changed", this.debounce(this.fillInAddress.bind(this), 500));
            this.street_address.setAttribute("subscribed", 'true')


        }


    }
    fillInAddress() {
        // Get the place details from the autocomplete object.
        const place = this.autocomplete.getPlace();
        let address1 = "";
        let postcode = "";
        let state = "";
        let country = "";
        let locality = ""

        if (!place) {
            return
        }

        for (const component of place.address_components) {
            // @ts-ignore remove once typings fixed
            const componentType = component.types[0];

            switch (componentType) {
                case "street_number": {
                    address1 = `${component.long_name} ${address1}`;
                    break;
                }

                case "route": {
                    address1 += component.short_name;
                    break;
                }

                case "postal_code": {
                    postcode = `${component.long_name}${postcode}`;
                    break;
                }

                case "postal_code_suffix": {
                    postcode = `${postcode}-${component.long_name}`;
                    break;
                }
                case "locality":
                    locality = component.long_name;
                    break;
                case "administrative_area_level_1": {
                    state = component.long_name;
                    break;
                }
                case "country" :
                    country = component.long_name;
                    break;
            }
        }


        if (this.street_address && address1) {
            this.street_address.value = address1
        }

        if (this.city_input && locality) {
            this.city_input.value = locality
        }


        if (this.state_input && state) {
            this.state_input.value = state
        }


        if (this.zip_input && postcode) {
            this.zip_input.value = postcode
        }


        if (this.country_input && country) {
            this.country_input.value = country
        }
        return
    }

    debounce(func, wait, immediate) {
        var timeout;
        return function () {
            var context = this, args = arguments;
            var later = function () {
                timeout = null;
                if (!immediate) func.apply(context, args);
            };
            var callNow = immediate && !timeout;
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
            if (callNow) func.apply(context, args);
        };
    }
}

export const autoCompleteField = {
    ...charField,
    component: AddressAutoComplete,
};


registry.category("fields").add("address_autocomplete", autoCompleteField);
