from odoo import models, api
import logging

_logger = logging.getLogger(__name__)

class SaleOrderAssignment(models.Model):
    _inherit = 'sale.order'

    @api.model_create_multi
    def create(self, vals_list):
        """Override create to assign team from delivery address on new orders"""
        orders = super(SaleOrderAssignment, self).create(vals_list)
        for order in orders:
            if order.state == 'draft':
                self._assign_team_from_address(order)
        return orders

    def write(self, vals):
        """
        Prevent team_id changes on confirmed/done orders
        EXCEPT for users in the Sales Manager group.
        Re-run assignment when shipping address changes.
        """
        if 'team_id' in vals:
            # Sales Managers can always change Sales Team
            is_sales_manager = self.env.user.has_group('OEL_sales_team_restriction.group_sale_team_manager')

            if not is_sales_manager:
                locked = self.filtered(lambda o: o.state in ('sale', 'done'))
                if locked:
                    _logger.info(
                        "Preventing team_id change on confirmed/done orders: %s",
                        locked.mapped('name')
                    )
                    vals = vals.copy()
                    vals.pop('team_id', None)

        res = super().write(vals)

        # Always reassign team when shipping address changes, regardless of order state
        if 'partner_shipping_id' in vals:
            _logger.info(
                "Reassigning teams for orders after shipping address change: %s",
                self.mapped('name')
            )
            for order in self:
                self._assign_team_from_address(order)

        return res

    def action_confirm(self):
        """Override action_confirm to assign team one final time before confirmation"""
        original_teams = {order.id: order.team_id.name if order.team_id else 'None' for order in self}
        res = super(SaleOrderAssignment, self).action_confirm()
        confirmed_orders = self.filtered(lambda o: o.state in ('sale', 'done'))
        
        for order in confirmed_orders:
            old_team = original_teams.get(order.id, 'None')
            self._assign_team_from_address(order)
            new_team = order.team_id.name if order.team_id else 'None'
            if old_team != new_team:
                _logger.info(
                    "Order %s: Team changed from '%s' to '%s' on confirmation",
                    order.name, old_team, new_team
                )
        
        return res

    def _assign_team_from_address(self, order):
        """
        Assign sales team based on delivery address team, with customer override option.
        
        Logic:
        1. Check if customer has 'No Ship To Assignment' checkbox enabled - if yes, skip custom logic
        2. Try to get team from delivery address (partner_shipping_id)
        3. Fall back to customer's primary team if delivery address has no team
        4. Only update if the team actually changes
        """
        try:
            # Check if customer has "No Ship To Assignment" checkbox enabled
            if hasattr(order.partner_id, 'x_studio_no_ship_to_assignment') and order.partner_id.x_studio_no_ship_to_assignment:
                _logger.debug(
                    "Order %s: Skipping custom team assignment - customer has 'No Ship To Assignment' enabled.",
                    order.name
                )
                return  # Skip custom assignment, use standard Odoo behavior

            # Get delivery address
            addr = order.partner_shipping_id
            
            # Determine team: delivery address team first, then customer team
            team = addr.team_id if addr and addr.team_id else order.partner_id.team_id
            
            # Get current and new team IDs for comparison
            current_team_id = order.team_id.id if order.team_id else False
            new_team_id = team.id if team else False
            
            # Only update if team actually changes
            if current_team_id != new_team_id:
                order.sudo().write({'team_id': new_team_id})
                team_name = team.name if team else 'None'
                _logger.debug(
                    "Order %s: Team assigned to '%s' based on delivery address",
                    order.name, team_name
                )
            else:
                _logger.debug("Order %s: Team assignment unchanged", order.name)
                
        except Exception as e:
            _logger.error("Error assigning team for order %s: %s", order.name, str(e))
