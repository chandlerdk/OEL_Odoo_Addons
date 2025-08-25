from odoo import models

class ResPartner(models.Model):
    _inherit = 'res.partner'

    # The field x_studio_no_ship_to_assignment is created via Studio
    # No need to define it here since it already exists in the database
    
    # This class exists to maintain proper module structure
    # and can be extended with additional partner-related customizations
    pass
