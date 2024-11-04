from odoo import models, fields, api
import logging


_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = "stock.picking"