# -*- coding: utf-8 -*-
from odoo import models, api
from odoo.exceptions import AccessError

class SaleOrder(models.Model):
    _inherit = 'sale.order'
