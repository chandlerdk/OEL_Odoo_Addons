Setup default carrier for customers:

technical>company properties> search for property_delivery_carrier_id
The one with no resource, will be default for all contacts
Set the value as "delivery.carrier, 128", here 128 is ID for the carrier

Setup the shipstation delivery method:
provider: ShipStation
Integration level: Get Rate and Create Shipment
weight uom: "lbs"
set carrier type to: "ups,stamps_com" or "ups_walleted,stamps_com"