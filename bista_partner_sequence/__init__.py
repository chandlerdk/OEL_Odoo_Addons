# -*- coding: utf-8 -*-
##############################################################################
#
# Bista Solutions Pvt. Ltd
# Copyright (C) 2023 (https://www.bistasolutions.com)
#
##############################################################################

from . import models


def _assign_partner_reference_no(env):
    partners = env['res.partner'].with_context(active_test=False).search(
        [('partner_reference_no', 'in', [False, 'New'])], order='id')
    for partner in partners:
        partner.partner_reference_no = env['ir.sequence'].next_by_code('res.partner')
