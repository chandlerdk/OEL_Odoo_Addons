# -*- coding: utf-8 -*-
# See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class CRMClaimRejectMessage(models.Model):
    _name = 'claim.reject.message'
    _description = 'CRM Claim Reject Message'

    name = fields.Char("Reject Reason", required=True)


class CRMReason(models.Model):
    _name = 'rma.reason.ept'
    _description = 'CRM Reason'

    name = fields.Char("RMA Reason", required=True)
    action = fields.Selection([
        ('refund', 'Refund'),
        ('replace_same_product', 'Replace With Same Product'),
        ('replace_other_product', 'Replace With Other Product'),
        ('repair', 'Repair')], string="Related Action", required=True)
