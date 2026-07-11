# -*- coding: utf-8 -*-

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class SquareLocation(models.Model):
    _name = 'square.location'
    _description = 'Square Location Mapping'
    _rec_name = 'name'
    _order = 'name'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company',
        required=True,
        default=lambda self: self.env.company,
        ondelete='cascade',
    )
    config_id = fields.Many2one(
        'square.config',
        string='Square Configuration',
        required=True,
        ondelete='cascade',
    )
    square_environment = fields.Selection(
        selection=[
            ('sandbox', 'Sandbox'),
            ('production', 'Production'),
        ],
        string='Square Environment',
        default='sandbox',
        required=True,
        help='Sandbox or Production account this location was imported from.',
    )
    square_location_id = fields.Char(
        string='Square Location ID',
        required=True,
        index=True,
    )
    status = fields.Selection(
        selection=[
            ('ACTIVE', 'Active'),
            ('INACTIVE', 'Inactive'),
        ],
        default='ACTIVE',
    )
    address_line_1 = fields.Char()
    locality = fields.Char(string='City')
    country_code = fields.Char(string='Country Code')
    warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Odoo Warehouse',
        domain="[('company_id', '=', company_id)]",
        help='Maps this Square location to an Odoo warehouse.',
    )
    stock_location_id = fields.Many2one(
        'stock.location',
        string='Stock Location',
        domain="[('usage', '=', 'internal'), ('company_id', 'in', [False, company_id])]",
        help='Internal stock location used for inventory sync. '
             'Defaults to the warehouse stock location when a warehouse is set.',
    )
    pos_config_ids = fields.Many2many(
        'pos.config',
        string='POS Configurations',
        help='POS sessions using this Square location for terminal payments.',
    )

    _sql_constraints = [
        (
            'square_location_company_uniq',
            'unique(square_location_id, config_id)',
            'Each Square location can only be mapped once per configuration.',
        ),
    ]

    @api.onchange('warehouse_id')
    def _onchange_warehouse_id(self):
        if self.warehouse_id:
            self.stock_location_id = self.warehouse_id.lot_stock_id

    @api.model
    def find_by_square_id(self, square_location_id, company=None):
        """Resolve a Square location ID to an Odoo square.location record."""
        domain = [('square_location_id', '=', square_location_id), ('active', '=', True)]
        if company:
            domain.append(('company_id', '=', company.id))
        location = self.search(domain, limit=1)
        if not location:
            _logger.warning(
                'No Odoo mapping found for Square location %s',
                square_location_id,
            )
        return location

    def get_stock_location(self):
        """Return the effective stock.location for inventory operations."""
        self.ensure_one()
        if self.stock_location_id:
            return self.stock_location_id
        if self.warehouse_id:
            return self.warehouse_id.lot_stock_id
        return self.env['stock.location']
