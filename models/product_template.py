# -*- coding: utf-8 -*-

import logging
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def _strip_none(value):
    """Remove None values so Square API payloads stay clean."""
    if isinstance(value, dict):
        return {k: _strip_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_none(item) for item in value]
    return value


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    square_item_id = fields.Char(
        string='Square Item ID',
        copy=False,
        index=True,
        help='Square catalog ITEM object ID.',
    )
    needs_square_sync = fields.Boolean(
        string='Sync to Square',
        default=True,
        help='When enabled, this product will be pushed to Square (Odoo → Square).',
    )
    square_last_sync = fields.Datetime(readonly=True)
    square_sync_error = fields.Text(readonly=True)

    def action_push_to_square(self):
        for template in self:
            template._push_to_square()
        return True

    def _square_currency(self):
        self.ensure_one()
        currency = (self.company_id or self.env.company).currency_id.name
        return currency or 'USD'

    def _push_to_square(self):
        self.ensure_one()
        company = self.company_id or self.env.company
        config = self.env['square.config'].get_active_config(company)
        if not config.sync_products:
            raise UserError(_('Product sync is disabled in Square configuration.'))
        if not config.oauth_connected:
            raise UserError(_('Square is not connected. Connect with Square first.'))

        client = config._get_api_client()
        idempotency_key = str(uuid.uuid4())
        currency = self._square_currency()
        item_id = self.square_item_id or f'#odoo_item_{self.id}'

        variations = []
        for variant in self.product_variant_ids:
            variation_id = variant.square_variation_id or f'#odoo_var_{variant.id}'
            sku = (variant.default_code or f'ODOO-{variant.id}')[:255]
            variation_name = (variant.name or self.name or 'Variant')[:512]
            price_money = {
                'amount': int(round(variant.lst_price * 100)),
                'currency': currency,
            }
            variations.append({
                'type': 'ITEM_VARIATION',
                'id': variation_id,
                'item_variation_data': {
                    'item_id': item_id,
                    'name': variation_name,
                    'sku': sku,
                    'pricing_type': 'FIXED_PRICING',
                    'price_money': price_money,
                    'track_inventory': self.type == 'product',
                },
            })

        item_data = {
            'name': (self.name or 'Product')[:512],
            'variations': variations,
        }
        description = (self.description_sale or '').strip()
        if description:
            item_data['description'] = description[:4096]

        catalog_object = _strip_none({
            'type': 'ITEM',
            'id': item_id,
            'item_data': item_data,
        })

        try:
            result = client.post('/v2/catalog/batch-upsert', {
                'idempotency_key': idempotency_key,
                'batches': [{'objects': [catalog_object]}],
            })
        except UserError as exc:
            self.write({'square_sync_error': str(exc)})
            raise

        id_mappings = result.get('id_mappings', [])
        mapping = {m.get('client_object_id'): m.get('object_id') for m in id_mappings}

        square_item_id = mapping.get(item_id)
        if square_item_id:
            self.square_item_id = square_item_id

        for variant in self.product_variant_ids:
            var_client_id = variant.square_variation_id or f'#odoo_var_{variant.id}'
            square_var_id = mapping.get(var_client_id)
            if square_var_id:
                variant.square_variation_id = square_var_id

        self.write({
            'square_last_sync': fields.Datetime.now(),
            'square_sync_error': False,
        })
        _logger.info(
            'Product %s synced to Square (item: %s)',
            self.id,
            self.square_item_id,
        )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if self.env.context.get('square_import'):
            return records
        for record in records.filtered(lambda p: p.needs_square_sync and p.sale_ok):
            try:
                record._push_to_square()
            except Exception as exc:
                _logger.warning('Auto Square sync failed for product %s: %s', record.id, exc)
        return records

    def write(self, vals):
        res = super().write(vals)
        if self.env.context.get('square_import'):
            return res
        sync_fields = {
            'name', 'lst_price', 'default_code', 'description_sale',
            'image_1920', 'needs_square_sync', 'sale_ok',
        }
        if sync_fields & set(vals.keys()):
            for record in self.filtered(lambda p: p.needs_square_sync and p.sale_ok):
                try:
                    record._push_to_square()
                except Exception as exc:
                    _logger.warning('Square sync on write failed for product %s: %s', record.id, exc)
        return res

    @api.model
    def _get_square_variant_attribute(self):
        attribute = self.env['product.attribute'].search([
            ('name', '=', 'Square Variant'),
        ], limit=1)
        if not attribute:
            attribute = self.env['product.attribute'].create({
                'name': 'Square Variant',
                'create_variant': 'always',
            })
        return attribute

    @api.model
    def _resolve_square_variations(self, item, related_by_id):
        """Return full ITEM_VARIATION catalog objects for a Square ITEM."""
        variations = []
        item_data = item.get('item_data') or {}
        for var_ref in item_data.get('variations', []):
            if var_ref.get('item_variation_data'):
                variations.append(var_ref)
                continue
            var_id = var_ref.get('id')
            if var_id and var_id in related_by_id:
                variations.append(related_by_id[var_id])
        if not variations and item.get('id') in related_by_id:
            related = related_by_id[item['id']]
            if related.get('type') == 'ITEM_VARIATION':
                variations.append(related)
        return variations

    @api.model
    def _square_variation_price(self, variation):
        var_data = variation.get('item_variation_data') or {}
        price_money = var_data.get('price_money') or {}
        amount = price_money.get('amount', 0) or 0
        try:
            return float(amount) / 100.0
        except (TypeError, ValueError):
            return 0.0

    @api.model
    def _square_variation_sku(self, variation, fallback):
        var_data = variation.get('item_variation_data') or {}
        return (var_data.get('sku') or fallback or '')[:255]

    @api.model
    def _square_variation_tracks_inventory(self, variation):
        var_data = variation.get('item_variation_data') or {}
        return bool(var_data.get('track_inventory', False))

    @api.model
    def _import_from_square_item(self, item, related_by_id, company):
        """Create or update an Odoo product from a Square catalog ITEM."""
        item_id = item.get('id')
        if not item_id:
            raise UserError(_('Square item is missing an ID.'))

        item_data = item.get('item_data') or {}
        item_name = (item_data.get('name') or _('Square Product'))[:255]
        description = (item_data.get('description') or '').strip()
        variations = self._resolve_square_variations(item, related_by_id)
        if not variations:
            raise UserError(_('Square item "%s" has no variations.') % item_name)

        Template = self.env['product.template'].with_context(square_import=True)
        template = Template.search([
            ('square_item_id', '=', item_id),
            '|', ('company_id', '=', False), ('company_id', '=', company.id),
        ], limit=1)

        track_inventory = any(
            self._square_variation_tracks_inventory(v) for v in variations
        )
        product_type = 'product' if track_inventory else 'consu'

        if len(variations) == 1:
            variation = variations[0]
            var_id = variation.get('id')
            var_data = variation.get('item_variation_data') or {}
            var_name = (var_data.get('name') or item_name)[:255]
            price = self._square_variation_price(variation)
            sku = self._square_variation_sku(variation, f'SQ-{item_id[:8]}')

            vals = {
                'name': item_name,
                'description_sale': description,
                'list_price': price,
                'sale_ok': True,
                'square_item_id': item_id,
                'needs_square_sync': False,
                'company_id': company.id,
                'type': product_type,
                'square_last_sync': fields.Datetime.now(),
                'square_sync_error': False,
            }

            if template:
                template.write(vals)
                variant = template.product_variant_ids[:1]
                if variant:
                    variant.write({
                        'square_variation_id': var_id,
                        'default_code': sku,
                        'lst_price': price,
                    })
                return 'updated'

            template = Template.create(vals)
            variant = template.product_variant_ids[:1]
            if variant and var_id:
                variant.write({
                    'square_variation_id': var_id,
                    'default_code': sku,
                    'lst_price': price,
                })
            return 'created'

        # Multiple Square variations → Odoo attribute variants
        attribute = self._get_square_variant_attribute()
        value_records = []
        variation_by_name = {}
        for variation in variations:
            var_data = variation.get('item_variation_data') or {}
            var_name = (var_data.get('name') or item_name)[:255]
            variation_by_name[var_name] = variation
            value = self.env['product.attribute.value'].search([
                ('attribute_id', '=', attribute.id),
                ('name', '=', var_name),
            ], limit=1)
            if not value:
                value = self.env['product.attribute.value'].create({
                    'attribute_id': attribute.id,
                    'name': var_name,
                })
            value_records.append(value)

        base_vals = {
            'name': item_name,
            'description_sale': description,
            'sale_ok': True,
            'square_item_id': item_id,
            'needs_square_sync': False,
            'company_id': company.id,
            'type': product_type,
            'square_last_sync': fields.Datetime.now(),
            'square_sync_error': False,
        }

        if template:
            template.write(base_vals)
            action = 'updated'
        else:
            create_vals = dict(base_vals, attribute_line_ids=[(0, 0, {
                'attribute_id': attribute.id,
                'value_ids': [(6, 0, [v.id for v in value_records])],
            })])
            template = Template.create(create_vals)
            action = 'created'

        for variant in template.product_variant_ids:
            label = variant.product_template_attribute_value_ids[:1].name
            if not label:
                label = variant.name
            variation = variation_by_name.get(label)
            if not variation:
                continue
            var_id = variation.get('id')
            price = self._square_variation_price(variation)
            sku = self._square_variation_sku(variation, f'SQ-{var_id[:8] if var_id else ""}')
            variant.write({
                'square_variation_id': var_id,
                'default_code': sku,
                'lst_price': price,
            })

        return action


class ProductProduct(models.Model):
    _inherit = 'product.product'

    square_variation_id = fields.Char(
        string='Square Variation ID',
        copy=False,
        index=True,
        help='Square catalog ITEM_VARIATION object ID.',
    )
    needs_square_sync = fields.Boolean(
        related='product_tmpl_id.needs_square_sync',
        readonly=False,
    )
    square_item_id = fields.Char(
        related='product_tmpl_id.square_item_id',
        readonly=True,
    )

    def action_push_to_square(self):
        return self.mapped('product_tmpl_id').action_push_to_square()

    @api.model
    def apply_square_inventory_update(self, variation_id, square_location_id, quantity):
        """Apply inventory.count.updated webhook data to Odoo stock (Square → Odoo)."""
        product = self.search([('square_variation_id', '=', variation_id)], limit=1)
        if not product:
            _logger.warning('No product found for Square variation %s', variation_id)
            return False

        square_location = self.env['square.location'].find_by_square_id(square_location_id)
        if not square_location:
            return False

        stock_location = square_location.get_stock_location()
        if not stock_location:
            _logger.warning('No stock location mapped for Square location %s', square_location_id)
            return False

        quants = self.env['stock.quant'].sudo().search([
            ('product_id', '=', product.id),
            ('location_id', '=', stock_location.id),
        ])
        current_qty = sum(quants.mapped('quantity'))
        delta = quantity - current_qty

        if abs(delta) < 0.0001:
            return True

        self.env['stock.quant']._update_available_quantity(
            product,
            stock_location,
            delta,
        )
        _logger.info(
            'Square inventory webhook: product %s at %s adjusted by %s (new qty: %s)',
            product.id,
            stock_location.id,
            delta,
            quantity,
        )
        return True
