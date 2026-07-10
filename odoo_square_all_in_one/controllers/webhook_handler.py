# -*- coding: utf-8 -*-

import json
import logging

from odoo import http
from odoo.http import request

from ..models.square_config import get_webhook_signature_key, verify_square_webhook_signature

_logger = logging.getLogger(__name__)


class SquareWebhookController(http.Controller):

    @http.route(
        '/payment/square/webhook',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False,
        save_session=False,
    )
    def square_webhook(self):
        """
        Square webhook endpoint.

        Handles inventory.count.updated and order.updated events.
        Validates X-Square-HmacSha256-Signature when configured.
        """
        raw_body = request.httprequest.get_data(as_text=True)
        signature = request.httprequest.headers.get('X-Square-HmacSha256-Signature', '')
        notification_url = request.httprequest.url

        config = request.env['square.config'].sudo().search([
            ('active', '=', True),
        ], limit=1)

        if not config:
            _logger.error('Square webhook received but no active configuration exists.')
            return request.make_response('No configuration', status=503)

        signature_key = get_webhook_signature_key(request.env)

        if signature_key:
            if not verify_square_webhook_signature(
                notification_url,
                raw_body,
                signature,
                signature_key,
            ):
                _logger.warning('Square webhook signature validation failed.')
                return request.make_response('Invalid signature', status=401)
        else:
            _logger.warning(
                'Square webhook signature key not configured; skipping validation.',
            )

        try:
            payload = json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError:
            _logger.error('Square webhook: invalid JSON body.')
            return request.make_response('Invalid JSON', status=400)

        event_type = payload.get('type', '')
        data = payload.get('data', {}).get('object', {})

        _logger.info('Square webhook received: type=%s', event_type)

        try:
            if event_type == 'inventory.count.updated':
                self._handle_inventory_count_updated(data)
            elif event_type == 'order.updated':
                self._handle_order_updated(data)
            else:
                _logger.debug('Square webhook ignored: unhandled type %s', event_type)
        except Exception:
            _logger.exception('Square webhook processing failed for type %s', event_type)
            return request.make_response('Processing error', status=500)

        return request.make_response('OK', status=200)

    def _handle_inventory_count_updated(self, data):
        """Parse inventory.count.updated and adjust Odoo stock."""
        counts = []
        if 'inventory_counts' in data:
            counts = data['inventory_counts']
        elif 'catalog_object_id' in data:
            counts = [data]

        ProductProduct = request.env['product.product'].sudo()
        for count in counts:
            variation_id = count.get('catalog_object_id')
            location_id = count.get('location_id')
            quantity = count.get('quantity')
            if not all([variation_id, location_id, quantity is not None]):
                _logger.warning('Incomplete inventory.count.updated payload: %s', count)
                continue
            try:
                qty = float(quantity)
            except (TypeError, ValueError):
                _logger.warning('Invalid quantity in inventory webhook: %s', quantity)
                continue
            ProductProduct.apply_square_inventory_update(variation_id, location_id, qty)

    def _handle_order_updated(self, data):
        """Parse order.updated for payment/order tracking."""
        order_data = data.get('order_updated', data)
        if 'order_id' in order_data and 'state' not in order_data:
            config = request.env['square.config'].sudo().search([('active', '=', True)], limit=1)
            if config:
                client = config._get_api_client()
                result = client.get(f'/v2/orders/{order_data["order_id"]}')
                order_data = result.get('order', order_data)

        request.env['payment.transaction'].sudo()._square_handle_order_updated(order_data)


class SquarePaymentController(http.Controller):

    @http.route(
        '/payment/square/return',
        type='http',
        auth='public',
        methods=['GET', 'POST'],
        csrf=False,
        save_session=False,
    )
    def square_return(self, reference=None, **kwargs):
        """Customer return URL after Square Hosted Checkout."""
        if not reference:
            _logger.warning('Square return: missing reference parameter.')
            return request.redirect('/payment/status')

        tx = request.env['payment.transaction'].sudo().search([
            ('reference', '=', reference),
            ('provider_code', '=', 'square'),
        ], limit=1)

        if not tx:
            _logger.warning('Square return: transaction not found for %s', reference)
            return request.redirect('/payment/status')

        payment_id = kwargs.get('payment_id') or kwargs.get('transactionId')
        order_id = kwargs.get('order_id') or tx.square_order_id

        verified = tx._square_verify_payment(payment_id=payment_id, order_id=order_id)
        if verified:
            tx._square_finalize_sale_order()

        return request.redirect('/payment/status')
