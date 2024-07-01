# -*- coding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (https://www.bistasolutions.com)
#
##############################################################################

from odoo import api, fields, models
from dateutil import relativedelta

from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

