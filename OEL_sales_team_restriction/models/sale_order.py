# -*- coding: utf-8 -*-
from odoo import models, SUPERUSER_ID

class SaleOrder(models.Model):
    _inherit = 'sale.order'
