# -*- coding: utf-8 -*-

import logging
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PosPaymentMethod(models.Model):
    _inherit = 'pos.payment.method'

    square_location_id = fields.Many2one(
        'square.location',
        string='Square Location',
        domain="[('company_id', '=', company_id)]",
    )
    square_device_id = fields.Char(
        string='Square Device ID',
        help='Square Terminal device ID from the Devices API.',
    )

    def _get_payment_terminal_selection(self):
        return super()._get_payment_terminal_selection() + [
            ('square', _('Square Terminal')),
        ]

    @api.constrains('use_payment_terminal', 'square_device_id', 'square_location_id')
    def _check_square_terminal_config(self):
        for method in self.filtered(lambda m: m.use_payment_terminal == 'square'):
            if not method.square_location_id:
                raise UserError(
                    _('Square Location is required for payment method "%s".') % method.name
                )
            if not method.square_device_id:
                raise UserError(
                    _('Square Device ID is required for payment method "%s".') % method.name
                )

    def square_terminal_checkout(self, amount, currency_name, reference, pos_config_id):
        """
        Initiate a Square Terminal checkout and poll until completion.

        Called from POS JavaScript via RPC. Blocks until terminal responds.
        """
        self.ensure_one()
        if self.use_payment_terminal != 'square':
            raise UserError(_('Payment method is not configured for Square Terminal.'))

        config = self.env['square.config'].get_active_config(self.company_id)
        client = config._get_api_client()
        location_id = self.square_location_id.square_location_id
        device_id = self.square_device_id
        idempotency_key = str(uuid.uuid4())
        amount_cents = int(round(amount * 100))

        payload = {
            'idempotency_key': idempotency_key,
            'checkout': {
                'amount_money': {
                    'amount': amount_cents,
                    'currency': currency_name,
                },
                'device_options': {
                    'device_id': device_id,
                    'skip_receipt_screen': False,
                    'tip_settings': {
                        'allow_tipping': True,
                    },
                },
                'reference_id': reference,
                'note': _('Odoo POS Order %s') % reference,
            },
        }

        result = client.post('/v2/terminals/checkouts', payload)
        checkout = result.get('checkout', {})
        checkout_id = checkout.get('id')
        if not checkout_id:
            raise UserError(_('Square Terminal did not return a checkout ID.'))

        _logger.info(
            'Square Terminal checkout initiated: %s (device: %s, amount: %s %s)',
            checkout_id,
            device_id,
            amount,
            currency_name,
        )

        final_checkout = self._square_poll_terminal_checkout(client, checkout_id)
        status = final_checkout.get('status')

        if status == 'COMPLETED':
            payment_ids = final_checkout.get('payment_ids') or []
            return {
                'status': 'success',
                'checkout_id': checkout_id,
                'payment_ids': payment_ids,
                'message': _('Payment completed on Square Terminal.'),
            }
        if status == 'CANCELED':
            return {
                'status': 'cancel',
                'checkout_id': checkout_id,
                'message': _('Payment canceled on terminal.'),
            }
        return {
            'status': 'error',
            'checkout_id': checkout_id,
            'message': _('Terminal checkout failed with status: %s') % status,
        }

    def _square_poll_terminal_checkout(self, client, checkout_id, max_attempts=120, interval=2):
        """Poll Square Terminal checkout until terminal state is terminal."""
        import time

        for attempt in range(max_attempts):
            result = client.get(f'/v2/terminals/checkouts/{checkout_id}')
            checkout = result.get('checkout', {})
            status = checkout.get('status')
            if status in ('COMPLETED', 'CANCELED', 'FAILED'):
                _logger.info(
                    'Square Terminal checkout %s finished with status %s (attempt %s)',
                    checkout_id,
                    status,
                    attempt + 1,
                )
                return checkout
            time.sleep(interval)

        raise UserError(
            _('Square Terminal checkout timed out. Please verify the device status.')
        )

    def square_terminal_cancel_checkout(self, checkout_id):
        """Cancel an in-progress terminal checkout."""
        self.ensure_one()
        config = self.env['square.config'].get_active_config(self.company_id)
        client = config._get_api_client()
        client.post(f'/v2/terminals/checkouts/{checkout_id}/cancel', {})
        _logger.info('Square Terminal checkout %s canceled', checkout_id)
        return True
