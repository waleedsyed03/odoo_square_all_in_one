# -*- coding: utf-8 -*-

import logging
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    square_checkout_id = fields.Char(string='Square Checkout ID', readonly=True, copy=False)
    square_order_id = fields.Char(string='Square Order ID', readonly=True, copy=False)
    square_payment_id = fields.Char(string='Square Payment ID', readonly=True, copy=False)

    def _get_specific_rendering_values(self, processing_values):
        res = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != 'square':
            return res
        redirect_url = self._square_create_checkout_link()
        return {
            **res,
            'api_url': redirect_url,
            'square_checkout_url': redirect_url,
        }

    def _square_create_checkout_link(self):
        """Create a Square Hosted Checkout payment link and return the redirect URL."""
        self.ensure_one()
        if self.provider_code != 'square':
            raise UserError(_('Transaction is not a Square payment.'))

        provider = self.provider_id
        config = provider._square_get_config()
        client = config._get_api_client()
        location_id = provider._square_get_location_id()

        sale_order = self.sale_order_ids[:1]
        line_items = []
        if sale_order:
            for line in sale_order.order_line.filtered(lambda l: not l.display_type):
                line_items.append({
                    'name': line.name[:512],
                    'quantity': str(int(line.product_uom_qty)),
                    'base_price_money': {
                        'amount': int(round(line.price_unit * 100)),
                        'currency': self.currency_id.name,
                    },
                })
        else:
            line_items.append({
                'name': self.reference,
                'quantity': '1',
                'base_price_money': {
                    'amount': int(round(self.amount * 100)),
                    'currency': self.currency_id.name,
                },
            })

        base_url = self.get_base_url().rstrip('/')
        idempotency_key = str(uuid.uuid4())

        payload = {
            'idempotency_key': idempotency_key,
            'quick_pay': {
                'name': _('Order %s') % (sale_order.name if sale_order else self.reference),
                'price_money': {
                    'amount': int(round(self.amount * 100)),
                    'currency': self.currency_id.name,
                },
                'location_id': location_id,
            },
            'checkout_options': {
                'redirect_url': f'{base_url}/payment/square/return?reference={self.reference}',
                'merchant_support_email': self.company_id.email or None,
            },
            'pre_populated_data': {},
        }

        if line_items and sale_order:
            payload = {
                'idempotency_key': idempotency_key,
                'order': {
                    'location_id': location_id,
                    'reference_id': self.reference,
                    'line_items': line_items,
                },
                'checkout_options': payload['checkout_options'],
            }

        result = client.post('/v2/online-checkout/payment-links', payload)
        payment_link = result.get('payment_link', {})
        checkout_url = payment_link.get('url') or payment_link.get('long_url')
        order_id = (result.get('related_resources') or {}).get('orders', [{}])[0].get('id')

        self.write({
            'square_checkout_id': payment_link.get('id'),
            'square_order_id': order_id,
        })
        _logger.info(
            'Square checkout link created for tx %s: %s',
            self.reference,
            payment_link.get('id'),
        )
        return checkout_url

    def _square_verify_payment(self, payment_id=None, order_id=None):
        """Verify payment status via Square Payments/Orders API."""
        self.ensure_one()
        provider = self.provider_id
        config = provider._square_get_config()
        client = config._get_api_client()

        payment_id = payment_id or self.square_payment_id
        order_id = order_id or self.square_order_id

        if payment_id:
            result = client.get(f'/v2/payments/{payment_id}')
            payment = result.get('payment', {})
            status = payment.get('status')
            self.square_payment_id = payment.get('id')
            if status == 'COMPLETED':
                self.provider_reference = payment.get('id')
                self.square_payment_id = payment.get('id')
                self._set_done()
                return True
            if status in ('CANCELED', 'FAILED'):
                self._set_canceled(_('Square payment status: %s') % status)
                return False

        if order_id:
            result = client.get(f'/v2/orders/{order_id}')
            order = result.get('order', {})
            state = order.get('state')
            tenders = order.get('tenders') or []
            for tender in tenders:
                if tender.get('payment_id'):
                    self.square_payment_id = tender['payment_id']
            if state == 'COMPLETED' or any(t.get('type') == 'CARD' for t in tenders):
                self._set_done()
                return True

        return False

    def _square_finalize_sale_order(self):
        """Mark linked sale order as paid and generate invoice on success."""
        self.ensure_one()
        for order in self.sale_order_ids:
            if order.state in ('sale', 'done'):
                try:
                    invoices = order._create_invoices()
                    for invoice in invoices:
                        if invoice.state == 'draft':
                            invoice.action_post()
                    order.message_post(
                        body=_('Square payment confirmed. Transaction: %s') % self.reference,
                    )
                    _logger.info(
                        'Sale order %s finalized after Square payment %s',
                        order.name,
                        self.reference,
                    )
                except Exception:
                    _logger.exception(
                        'Failed to create invoice for order %s after Square payment',
                        order.name,
                    )

    @api.model
    def _square_handle_order_updated(self, order_data):
        """Process order.updated webhook payload."""
        reference = order_data.get('reference_id')
        order_id = order_data.get('id')
        state = order_data.get('state')

        tx = self.search([
            '|',
            ('reference', '=', reference),
            ('square_order_id', '=', order_id),
        ], limit=1)
        if not tx:
            _logger.info('Square order.updated: no matching transaction for %s', order_id)
            return

        tx.square_order_id = order_id
        tenders = order_data.get('tenders') or []
        for tender in tenders:
            if tender.get('payment_id'):
                tx.square_payment_id = tender['payment_id']

        if state == 'COMPLETED':
            tx._set_done()
            tx._square_finalize_sale_order()
        elif state == 'CANCELED':
            tx._set_canceled(_('Square order canceled.'))
        else:
            tx._set_pending()
