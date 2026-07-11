# -*- coding: utf-8 -*-

import logging
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    @api.model
    def _setup_provider(self, provider_code):
        if provider_code != 'square':
            return super()._setup_provider(provider_code)
        self._square_bootstrap_providers()

    @api.model
    def _square_bootstrap_providers(self):
        """Ensure the Square hosted-checkout provider exists for every company."""
        module = self.env.ref('base.module_odoo_square_all_in_one', raise_if_not_found=False)
        redirect_form = self.env.ref(
            'odoo_square_all_in_one.square_redirect_form',
            raise_if_not_found=False,
        )
        card_method = self.env.ref('payment.payment_method_card', raise_if_not_found=False)
        if not redirect_form or not card_method:
            return

        for company in self.env['res.company'].search([]):
            provider = self.search([
                ('code', '=', 'square'),
                ('company_id', '=', company.id),
            ], limit=1)
            if not provider:
                provider = self.search([
                    ('code', '=', 'square'),
                    ('company_id', '=', False),
                ], limit=1)
                if provider and not provider.company_id:
                    provider.company_id = company.id
                else:
                    provider = self.create({
                        'name': 'Square Hosted Checkout',
                        'code': 'square',
                        'company_id': company.id,
                        'state': 'disabled',
                        'is_published': False,
                    })

            vals = {
                'redirect_form_view_id': redirect_form.id,
                'payment_method_ids': [(6, 0, card_method.ids)],
            }
            if module:
                vals['module_id'] = module.id
            provider.write(vals)

            config = self.env['square.config'].search([
                ('company_id', '=', company.id),
                ('active', '=', True),
            ], limit=1)
            if config and config.oauth_connected:
                config._sync_payment_provider()

    def _square_sync_from_config(self, config):
        """Link this provider to a Square configuration and publish for website checkout."""
        self.ensure_one()
        if self.code != 'square':
            return

        location = config.default_location_id or config.location_ids[:1]
        provider_state = 'disabled'
        is_published = False
        if config.oauth_connected and location:
            provider_state = 'test' if config.environment == 'sandbox' else 'enabled'
            is_published = True

        redirect_form = self.env.ref('odoo_square_all_in_one.square_redirect_form')
        card_method = self.env.ref('payment.payment_method_card')
        module = self.env.ref('base.module_odoo_square_all_in_one', raise_if_not_found=False)

        vals = {
            'name': 'Square Hosted Checkout',
            'square_config_id': config.id,
            'square_location_id': location.id if location else False,
            'state': provider_state,
            'is_published': is_published,
            'redirect_form_view_id': redirect_form.id,
            'payment_method_ids': [(6, 0, card_method.ids)],
        }
        if module:
            vals['module_id'] = module.id
        self.write(vals)

    code = fields.Selection(
        selection_add=[('square', 'Square Hosted Checkout')],
        ondelete={'square': 'set default'},
    )
    square_config_id = fields.Many2one(
        'square.config',
        string='Square Configuration',
        domain="[('company_id', '=', company_id)]",
    )
    square_location_id = fields.Many2one(
        'square.location',
        string='Checkout Location',
        domain="[('config_id', '=', square_config_id)]",
        help='Square location used for hosted checkout payments.',
    )

    def _get_supported_currencies(self):
        supported = super()._get_supported_currencies()
        if self.code == 'square':
            supported = supported.filtered(
                lambda c: c.name in {'USD', 'CAD', 'GBP', 'AUD', 'EUR', 'JPY'}
            )
        return supported

    def _compute_feature_support_fields(self):
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == 'square').update({
            'support_express_checkout': False,
            'support_manual_capture': False,
            'support_refund': 'partial',
            'support_tokenization': False,
        })

    def _get_default_payment_method_codes(self):
        if self.code != 'square':
            return super()._get_default_payment_method_codes()
        return ['card']

    def _square_get_config(self):
        self.ensure_one()
        config = self.square_config_id or self.env['square.config'].search([
            ('company_id', '=', self.company_id.id),
            ('active', '=', True),
        ], limit=1)
        if not config:
            raise ValidationError(_('Square configuration is missing for provider %s.') % self.name)
        return config

    def _square_get_location_id(self):
        self.ensure_one()
        location = self.square_location_id or self._square_get_config().default_location_id
        if not location or not location.square_location_id:
            raise ValidationError(_('Square checkout location is not configured.'))
        return location.square_location_id

    def _get_specific_rendering_values(self, processing_values):
        res = super()._get_specific_rendering_values(processing_values)
        if self.code != 'square':
            return res

        tx = self.env['payment.transaction'].search([
            ('reference', '=', processing_values.get('reference')),
            ('provider_code', '=', 'square'),
        ], limit=1)
        if not tx:
            raise ValidationError(_('Square payment transaction was not found.'))

        config = provider._square_get_config()
        config._ensure_oauth_scopes(config._get_payment_oauth_scopes())
        redirect_url = tx._square_create_checkout_link()
        return {
            'api_url': redirect_url,
            'square_checkout_url': redirect_url,
        }

    def _get_redirect_form_view(self, is_validation=False):
        if self.code == 'square':
            return self.env.ref('odoo_square_all_in_one.square_redirect_form')
        return super()._get_redirect_form_view(is_validation=is_validation)

    def _process_notification_data(self, notification_data):
        super()._process_notification_data(notification_data)
        if self.code != 'square':
            return

        tx = self.env['payment.transaction'].search([
            ('reference', '=', notification_data.get('reference')),
            ('provider_code', '=', 'square'),
        ], limit=1)
        if not tx:
            _logger.warning('Square notification: transaction not found for %s', notification_data)
            return

        payment_status = notification_data.get('status', 'error')
        if payment_status == 'COMPLETED':
            tx._set_done()
            tx._square_finalize_sale_order()
        elif payment_status in ('CANCELED', 'FAILED'):
            tx._set_canceled(_('Square payment was %s.') % payment_status)
        else:
            tx._set_pending()

    def _send_refund_request(self, amount_to_refund, create_refund_transaction=True):
        if self.code != 'square':
            return super()._send_refund_request(amount_to_refund, create_refund_transaction)

        tx = self.env['payment.transaction'].search([
            ('provider_code', '=', 'square'),
            ('square_payment_id', '!=', False),
            ('sale_order_ids', 'in', self.env.context.get('active_id')),
        ], limit=1)

        if not tx or not tx.square_payment_id:
            _logger.error('Square refund: no payment ID on transaction %s', tx.reference if tx else 'N/A')
            return None

        config = self._square_get_config()
        client = config._get_api_client()
        idempotency_key = str(uuid.uuid4())
        currency = tx.currency_id.name
        amount_cents = int(round(amount_to_refund * 100))

        result = client.post('/v2/refunds', {
            'idempotency_key': idempotency_key,
            'payment_id': tx.square_payment_id,
            'amount_money': {
                'amount': amount_cents,
                'currency': currency,
            },
        })
        refund = result.get('refund', {})
        _logger.info('Square refund created: %s', refund.get('id'))
        return refund
