# -*- coding: utf-8 -*-
"""Square API client and configuration models."""

import hashlib
import hmac
import json
import logging
import uuid
from base64 import b64encode

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .square_oauth import (
    DEFAULT_OAUTH_WORKER_BASE,
    SQUARE_OAUTH_SCOPES_CATALOG,
    SQUARE_OAUTH_SCOPES_INVENTORY,
    SquareOAuthHelper,
)

_logger = logging.getLogger(__name__)

SQUARE_API_VERSION = '2024-10-17'
SANDBOX_BASE_URL = 'https://connect.squareupsandbox.com'
PRODUCTION_BASE_URL = 'https://connect.squareup.com'


class SquareAPIClient:
    """Lightweight Square REST API v2 client."""

    def __init__(self, access_token, environment='sandbox', timeout=30):
        self.access_token = access_token
        self.base_url = (
            PRODUCTION_BASE_URL if environment == 'production' else SANDBOX_BASE_URL
        )
        self.timeout = timeout

    def _headers(self):
        return {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json',
            'Square-Version': SQUARE_API_VERSION,
        }

    def request(self, method, endpoint, payload=None, params=None):
        url = f'{self.base_url}{endpoint}'
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=self._headers(),
                json=payload,
                params=params,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            _logger.exception('Square API network error on %s %s', method, endpoint)
            raise UserError(_('Square API connection failed: %s') % exc) from exc

        if not response.ok:
            _logger.error(
                'Square API error %s on %s %s: %s',
                response.status_code,
                method,
                endpoint,
                response.text,
            )
            raise UserError(
                _('Square API error (%(status)s): %(message)s') % {
                    'status': response.status_code,
                    'message': response.text,
                }
            )
        return response.json() if response.text else {}

    def get(self, endpoint, params=None):
        return self.request('GET', endpoint, params=params)

    def post(self, endpoint, payload=None):
        return self.request('POST', endpoint, payload=payload)

    def put(self, endpoint, payload=None):
        return self.request('PUT', endpoint, payload=payload)


def verify_square_webhook_signature(notification_url, body, signature, signature_key):
    """Validate X-Square-HmacSha256-Signature header."""
    if not signature or not signature_key:
        return False
    payload = (notification_url + body).encode('utf-8')
    digest = hmac.new(
        signature_key.encode('utf-8'),
        payload,
        hashlib.sha256,
    ).digest()
    expected = b64encode(digest).decode('utf-8')
    return hmac.compare_digest(expected, signature)


def get_webhook_signature_key(env):
    """Read webhook signing key from system parameters (not exposed in UI)."""
    return env['ir.config_parameter'].sudo().get_param(
        'odoo_square.webhook_signature_key', ''
    ) or ''


class SquareConfig(models.Model):
    _name = 'square.config'
    _description = 'Square Integration Configuration'
    _rec_name = 'name'

    name = fields.Char(required=True, default='Square Configuration')
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company',
        required=True,
        default=lambda self: self.env.company,
        ondelete='cascade',
    )
    application_id = fields.Char(
        string='Application ID',
        groups='base.group_system',
        copy=False,
        help='Set automatically after Connect with Square.',
    )
    access_token = fields.Char(
        string='Access Token',
        groups='base.group_system',
        copy=False,
        help='Set automatically after Connect with Square.',
    )
    oauth_site_key = fields.Char(
        string='OAuth Site Key',
        groups='base.group_system',
        copy=False,
    )
    oauth_registered_site_url = fields.Char(
        string='Registered Site URL',
        groups='base.group_system',
        copy=False,
    )
    oauth_refresh_token = fields.Char(
        string='OAuth Refresh Token',
        groups='base.group_system',
        copy=False,
    )
    oauth_expires_at = fields.Char(
        string='Token Expires At',
        groups='base.group_system',
        copy=False,
    )
    oauth_merchant_id = fields.Char(
        string='Square Merchant ID',
        readonly=True,
        copy=False,
    )
    oauth_token_refreshed_at = fields.Datetime(readonly=True, copy=False)
    oauth_connected = fields.Boolean(
        string='Square Connected',
        compute='_compute_oauth_connected',
        store=True,
    )
    environment = fields.Selection(
        selection=[
            ('sandbox', 'Sandbox'),
            ('production', 'Production'),
        ],
        required=True,
        default='sandbox',
    )
    default_location_id = fields.Many2one(
        'square.location',
        string='Default Square Location',
        domain="[('config_id', '=', id)]",
    )
    location_ids = fields.One2many(
        'square.location',
        'config_id',
        string='Square Locations',
    )
    sync_products = fields.Boolean(
        string='Push Products to Square',
        default=True,
        help='Push Odoo products to the Square catalog (Odoo → Square).',
    )
    sync_inventory = fields.Boolean(
        string='Push Inventory to Square',
        default=True,
        help='Push Odoo stock quantities to Square locations (Odoo → Square).',
    )
    import_products = fields.Boolean(
        string='Import Products from Square',
        default=True,
        help='Pull Square catalog items into Odoo (Square → Odoo).',
    )
    import_inventory = fields.Boolean(
        string='Import Inventory from Square',
        default=True,
        help='Pull Square stock counts into Odoo warehouses (Square → Odoo).',
    )
    last_sync_at = fields.Datetime(readonly=True)
    last_sync_status = fields.Selection(
        selection=[
            ('success', 'Success'),
            ('partial', 'Partial'),
            ('failed', 'Failed'),
        ],
        readonly=True,
    )
    last_sync_message = fields.Text(readonly=True)

    _sql_constraints = [
        (
            'company_uniq',
            'unique(company_id)',
            'Only one Square configuration is allowed per company.',
        ),
    ]

    @api.depends('access_token', 'oauth_refresh_token')
    def _compute_oauth_connected(self):
        for config in self:
            config.oauth_connected = bool(config.access_token)

    @api.constrains('access_token')
    def _check_credentials(self):
        for config in self:
            if config.access_token and len(config.access_token) < 16:
                raise ValidationError(_('Stored Square access token appears to be invalid.'))

    def _oauth_helper(self):
        self.ensure_one()
        icp = self.env['ir.config_parameter'].sudo()
        reg_secret = icp.get_param('odoo_square.oauth_worker_registration_secret', '') or ''
        return SquareOAuthHelper(
            self.env,
            worker_base=DEFAULT_OAUTH_WORKER_BASE,
            registration_secret=reg_secret,
        )

    def _get_site_base_url(self):
        self.ensure_one()
        return self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')

    def _ensure_worker_registered(self):
        self.ensure_one()
        site_url = SquareOAuthHelper.normalize_site_url(self._get_site_base_url())
        if not site_url:
            raise UserError(_('Set web.base.url in Odoo system parameters first.'))

        current = SquareOAuthHelper.normalize_site_url(self.oauth_registered_site_url or '')
        if self.oauth_site_key and current == site_url:
            return self.oauth_site_key

        site_key = self._oauth_helper().register_site(site_url)
        self.write({
            'oauth_site_key': site_key,
            'oauth_registered_site_url': site_url,
        })
        return site_key

    def action_connect_square(self):
        """Redirect merchant to Square OAuth via the existing Cloudflare Worker."""
        self.ensure_one()
        site_key = self._ensure_worker_registered()
        oauth = self._oauth_helper()
        return_state = oauth.create_return_state(self.id)
        return_url = (
            f'{self.get_base_url().rstrip("/")}/square/oauth/return'
            f'?config_id={self.id}&square_oauth_state={return_state}'
        )
        start_url = oauth.build_start_url(site_key, return_url, self.environment)
        return {
            'type': 'ir.actions.act_url',
            'url': start_url,
            'target': 'self',
        }

    def action_disconnect_square(self):
        self.ensure_one()
        if self.oauth_site_key and self.access_token:
            try:
                self._oauth_helper().revoke_token(
                    self.oauth_site_key,
                    self.access_token,
                    self.environment,
                )
            except Exception:
                _logger.warning('Square revoke failed for config %s', self.id)
        self.write({
            'access_token': False,
            'oauth_refresh_token': False,
            'oauth_expires_at': False,
            'oauth_merchant_id': False,
            'oauth_token_refreshed_at': False,
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Square'),
                'message': _('Square account disconnected.'),
                'type': 'warning',
                'sticky': False,
            },
        }

    def apply_oauth_token_response(self, data):
        """Persist tokens returned by the worker /claim or /refresh endpoints."""
        self.ensure_one()
        vals = {
            'access_token': data.get('access_token'),
            'oauth_refresh_token': data.get('refresh_token') or self.oauth_refresh_token,
            'oauth_expires_at': data.get('expires_at') or self.oauth_expires_at,
            'oauth_merchant_id': data.get('merchant_id') or self.oauth_merchant_id,
            'oauth_token_refreshed_at': fields.Datetime.now(),
        }
        if data.get('merchant_id') and not self.application_id:
            vals['application_id'] = data['merchant_id']
        self.write(vals)
        self._import_locations_from_square()
        if self.location_ids and not self.default_location_id:
            self.default_location_id = self.location_ids[:1]

    def _import_locations_from_square(self):
        """Fetch Square locations without returning a UI notification."""
        self.ensure_one()
        client = self._get_api_client()
        result = client.get('/v2/locations')
        SquareLocation = self.env['square.location']
        for loc_data in result.get('locations', []):
            square_id = loc_data.get('id')
            if not square_id:
                continue
            existing = SquareLocation.search([
                ('square_location_id', '=', square_id),
                ('config_id', '=', self.id),
            ], limit=1)
            vals = {
                'name': loc_data.get('name') or square_id,
                'square_location_id': square_id,
                'status': loc_data.get('status', 'ACTIVE'),
                'address_line_1': (loc_data.get('address') or {}).get('address_line_1'),
                'locality': (loc_data.get('address') or {}).get('locality'),
                'country_code': (loc_data.get('address') or {}).get('country'),
                'config_id': self.id,
                'company_id': self.company_id.id,
            }
            if existing:
                existing.write(vals)
            else:
                SquareLocation.create(vals)

    def _refresh_oauth_token_if_needed(self, buffer_seconds=600):
        self.ensure_one()
        if not self.oauth_refresh_token or not self.oauth_site_key:
            return

        expires_at = self.oauth_expires_at or ''
        should_refresh = not expires_at
        if expires_at:
            try:
                from datetime import datetime
                exp_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                should_refresh = fields.Datetime.now().timestamp() >= (exp_dt.timestamp() - buffer_seconds)
            except (ValueError, TypeError):
                should_refresh = True

        if not should_refresh:
            return

        data = self._oauth_helper().refresh_token(
            self.oauth_site_key,
            self.oauth_refresh_token,
            self.environment,
        )
        self.apply_oauth_token_response(data)

    @api.model
    def _cron_refresh_oauth_tokens(self):
        configs = self.search([
            ('active', '=', True),
            ('oauth_refresh_token', '!=', False),
        ])
        for config in configs:
            try:
                config._refresh_oauth_token_if_needed()
            except Exception:
                _logger.exception('OAuth token refresh failed for config %s', config.id)

    def _get_api_client(self):
        self.ensure_one()
        self._refresh_oauth_token_if_needed()
        if not self.access_token:
            raise UserError(_('Square is not connected. Click "Connect with Square" first.'))
        return SquareAPIClient(self.access_token, self.environment)

    def _ensure_fresh_oauth_token(self):
        """Refresh OAuth token before bulk API work when possible."""
        self.ensure_one()
        if not self.oauth_refresh_token or not self.oauth_site_key:
            return
        try:
            data = self._oauth_helper().refresh_token(
                self.oauth_site_key,
                self.oauth_refresh_token,
                self.environment,
            )
            self.apply_oauth_token_response(data)
        except UserError as exc:
            _logger.warning(
                'Could not refresh Square token before sync (config %s): %s',
                self.id,
                exc,
            )

    def _square_notification(self, title, message, notif_type='info', sticky=False):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'type': notif_type,
                'sticky': sticky,
            },
        }

    def _get_token_scopes(self):
        """Return OAuth scopes granted on the current Square access token."""
        self.ensure_one()
        client = self._get_api_client()
        try:
            result = client.post('/oauth2/token/status', {})
        except UserError:
            return []
        scopes = result.get('scopes') or []
        return [str(scope).upper() for scope in scopes]

    def _ensure_oauth_scopes(self, required_scopes):
        """Raise a clear error when the token lacks Square permissions."""
        self.ensure_one()
        required = {scope.upper() for scope in required_scopes}
        granted = set(self._get_token_scopes())
        if not granted:
            return
        missing = sorted(required - granted)
        if missing:
            raise UserError(_(
                'Square connection is missing permissions: %(scopes)s.\n\n'
                'Click **Disconnect Square**, then **Connect with Square** again '
                'and approve catalog/inventory access when Square asks.',
            ) % {'scopes': ', '.join(missing)})

    def _friendly_square_api_error(self, exc):
        """Turn Square API UserError into a merchant-friendly reconnect hint."""
        message = str(exc)
        if 'INSUFFICIENT_SCOPES' in message or 'ITEMS_WRITE' in message:
            return _(
                'Square did not grant catalog write permission (ITEMS_WRITE).\n\n'
                'Disconnect Square, then Connect with Square again and approve '
                'all requested permissions.'
            )
        if 'INVENTORY_WRITE' in message:
            return _(
                'Square did not grant inventory write permission (INVENTORY_WRITE).\n\n'
                'Disconnect Square, then Connect with Square again.'
            )
        return message

    def action_test_connection(self):
        self.ensure_one()
        client = self._get_api_client()
        result = client.get('/v2/locations')
        locations = result.get('locations', [])
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Square Connection'),
                'message': _('Connected successfully. Found %s location(s).') % len(locations),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_fetch_locations(self):
        self.ensure_one()
        self._import_locations_from_square()
        count = len(self.location_ids)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Locations Synced'),
                'message': _('Square locations updated (%s total).') % count,
                'type': 'success',
                'sticky': False,
            },
        }

    def action_sync_all_products(self):
        """Push saleable Odoo products to the Square catalog (Odoo → Square)."""
        self.ensure_one()
        if not self.oauth_connected:
            return self._square_notification(
                _('Square Not Connected'),
                _('Click "Connect with Square" before syncing products.'),
                'warning',
            )
        if not self.sync_products:
            return self._square_notification(
                _('Product Sync Disabled'),
                _('Enable "Sync Products to Square" in Sync Settings.'),
                'warning',
            )

        self._ensure_fresh_oauth_token()
        try:
            self._ensure_oauth_scopes(SQUARE_OAUTH_SCOPES_CATALOG)
        except UserError as exc:
            return self._square_notification(
                _('Missing Square Permissions'),
                str(exc),
                'warning',
                sticky=True,
            )

        products = self.env['product.template'].search([
            ('needs_square_sync', '=', True),
            ('sale_ok', '=', True),
            '|', ('company_id', '=', False), ('company_id', '=', self.company_id.id),
        ])
        if not products:
            return self._square_notification(
                _('No Products to Sync'),
                _(
                    'No saleable products are marked "Sync to Square". '
                    'Open a product → Square tab and enable sync, or create products first.'
                ),
                'warning',
            )

        success = errors = 0
        first_error = ''
        for product in products:
            try:
                product.with_company(self.company_id)._push_to_square()
                success += 1
            except UserError as exc:
                errors += 1
                friendly = self._friendly_square_api_error(exc)
                if not first_error:
                    first_error = friendly
                product.write({'square_sync_error': friendly})
                _logger.warning(
                    'Failed to sync product %s to Square: %s',
                    product.id,
                    exc,
                )
            except Exception as exc:
                errors += 1
                if not first_error:
                    first_error = str(exc)
                product.write({'square_sync_error': str(exc)})
                _logger.exception(
                    'Failed to sync product %s to Square',
                    product.id,
                )

        self._update_sync_status(errors, success, first_error)

        if errors and not success:
            return self._square_notification(
                _('Product Sync Failed'),
                first_error or _('All product syncs failed. Check Square connection and try again.'),
                'danger',
                sticky=True,
            )
        if errors:
            return self._square_notification(
                _('Product Sync Partial'),
                _('Synced %(ok)s product(s), %(fail)s failed. %(detail)s') % {
                    'ok': success,
                    'fail': errors,
                    'detail': first_error,
                },
                'warning',
                sticky=True,
            )
        return self._square_notification(
            _('Products Synced to Square'),
            _('Successfully pushed %(count)s product(s) to Square.') % {'count': success},
            'success',
        )

    def _fetch_square_catalog_items(self, client):
        """Download all ITEM objects from Square catalog (paginated search)."""
        items = []
        related_by_id = {}
        cursor = None
        while True:
            payload = {
                'object_types': ['ITEM'],
                'include_related_objects': True,
            }
            if cursor:
                payload['cursor'] = cursor
            result = client.post('/v2/catalog/search', payload)
            for obj in result.get('objects', []):
                if obj.get('type') == 'ITEM':
                    items.append(obj)
            for rel in result.get('related_objects', []):
                if rel.get('id'):
                    related_by_id[rel['id']] = rel
            cursor = result.get('cursor')
            if not cursor:
                break
        return items, related_by_id

    def action_import_products_from_square(self):
        """Pull Square catalog items into Odoo (Square → Odoo)."""
        self.ensure_one()
        if not self.oauth_connected:
            return self._square_notification(
                _('Square Not Connected'),
                _('Click "Connect with Square" before importing products.'),
                'warning',
            )
        if not self.import_products:
            return self._square_notification(
                _('Import Disabled'),
                _('Enable "Import Products from Square" in Sync Settings.'),
                'warning',
            )

        self._ensure_fresh_oauth_token()
        client = self._get_api_client()

        try:
            self._ensure_oauth_scopes(SQUARE_OAUTH_SCOPES_CATALOG)
            items, related_by_id = self._fetch_square_catalog_items(client)
        except UserError as exc:
            return self._square_notification(
                _('Import Failed'),
                str(exc),
                'danger',
                sticky=True,
            )

        if not items:
            return self._square_notification(
                _('No Square Products'),
                _('Your Square catalog has no items to import.'),
                'warning',
            )

        ProductTemplate = self.env['product.template']
        created = updated = errors = 0
        first_error = ''
        for item in items:
            try:
                result = ProductTemplate.with_company(self.company_id)._import_from_square_item(
                    item,
                    related_by_id,
                    self.company_id,
                )
                if result == 'created':
                    created += 1
                else:
                    updated += 1
            except Exception as exc:
                errors += 1
                if not first_error:
                    first_error = str(exc)
                _logger.exception(
                    'Failed to import Square item %s',
                    item.get('id'),
                )

        total = created + updated
        message = _('Imported from Square: %(created)s created, %(updated)s updated.') % {
            'created': created,
            'updated': updated,
        }
        if errors:
            message += ' ' + _('%(errors)s error(s).') % {'errors': errors}
            if first_error:
                message += ' ' + first_error[:300]
        status = 'success' if not errors else ('partial' if total else 'failed')
        self.write({
            'last_sync_at': fields.Datetime.now(),
            'last_sync_status': status,
            'last_sync_message': message,
        })

        if errors and not total:
            return self._square_notification(
                _('Import Failed'),
                first_error or _('Could not import any Square products.'),
                'danger',
                sticky=True,
            )
        if errors:
            return self._square_notification(
                _('Import Partial'),
                message,
                'warning',
                sticky=True,
            )
        return self._square_notification(
            _('Products Imported from Square'),
            message,
            'success',
        )

    def action_import_inventory_from_square(self):
        """Pull Square stock counts into Odoo (Square → Odoo)."""
        self.ensure_one()
        if not self.oauth_connected:
            return self._square_notification(
                _('Square Not Connected'),
                _('Click "Connect with Square" before importing inventory.'),
                'warning',
            )
        if not self.import_inventory:
            return self._square_notification(
                _('Import Disabled'),
                _('Enable "Import Inventory from Square" in Sync Settings.'),
                'warning',
            )

        self._ensure_fresh_oauth_token()
        try:
            self._ensure_oauth_scopes(SQUARE_OAUTH_SCOPES_INVENTORY)
            applied = self._import_inventory_from_square()
        except UserError as exc:
            return self._square_notification(
                _('Inventory Import Failed'),
                str(exc),
                'danger',
                sticky=True,
            )

        if applied == 0:
            return self._square_notification(
                _('Nothing to Import'),
                _(
                    'Map Square locations to warehouses, link products with Square '
                    'variation IDs, then try again.'
                ),
                'warning',
            )

        message = _('Imported %(count)s inventory count(s) from Square.') % {'count': applied}
        self.write({
            'last_sync_at': fields.Datetime.now(),
            'last_sync_status': 'success',
            'last_sync_message': message,
        })
        return self._square_notification(
            _('Inventory Imported from Square'),
            message,
            'success',
        )

    def _import_inventory_from_square(self):
        """Fetch Square inventory counts and apply them to mapped Odoo locations."""
        self.ensure_one()
        client = self._get_api_client()
        locations = self.location_ids.filtered(
            lambda loc: loc.active and loc.square_location_id and loc.get_stock_location()
        )
        if not locations:
            raise UserError(
                _('No Square locations mapped to Odoo stock locations. Use Fetch Locations first.')
            )

        products = self.env['product.product'].search([
            ('square_variation_id', '!=', False),
            ('type', '=', 'product'),
            '|', ('company_id', '=', False), ('company_id', '=', self.company_id.id),
        ])
        if not products:
            raise UserError(
                _('No Odoo products linked to Square variations. Import products from Square first.')
            )

        variation_ids = products.mapped('square_variation_id')
        square_location_ids = locations.mapped('square_location_id')
        applied = 0
        batch_size = 100

        for idx in range(0, len(variation_ids), batch_size):
            var_batch = variation_ids[idx:idx + batch_size]
            result = client.post('/v2/inventory/counts/batch-retrieve', {
                'catalog_object_ids': var_batch,
                'location_ids': square_location_ids,
            })
            for count in result.get('counts', []):
                variation_id = count.get('catalog_object_id')
                location_id = count.get('location_id')
                quantity = count.get('quantity')
                if variation_id and location_id and quantity is not None:
                    try:
                        qty = float(quantity)
                    except (TypeError, ValueError):
                        continue
                    if self.env['product.product'].apply_square_inventory_update(
                        variation_id, location_id, qty
                    ):
                        applied += 1

        _logger.info(
            'Imported %s inventory counts from Square for config %s',
            applied,
            self.id,
        )
        return applied

    @api.model
    def _cron_import_inventory_from_square(self):
        configs = self.search([('active', '=', True), ('import_inventory', '=', True)])
        for config in configs:
            try:
                config.action_import_inventory_from_square()
            except Exception:
                _logger.exception(
                    'Square inventory import cron failed for config %s',
                    config.id,
                )

    def _update_sync_status(self, errors, success, first_error=''):
        self.ensure_one()
        if errors and success:
            status = 'partial'
            message = _('Synced %s product(s), %s error(s).') % (success, errors)
        elif errors:
            status = 'failed'
            message = _('All %s product sync(s) failed.') % errors
        else:
            status = 'success'
            message = _('Successfully synced %s product(s) to Square.') % success
        if first_error:
            message = '%s %s' % (message, first_error[:500])
        self.write({
            'last_sync_at': fields.Datetime.now(),
            'last_sync_status': status,
            'last_sync_message': message,
        })

    @api.model
    def _cron_sync_products_to_square(self):
        configs = self.search([('active', '=', True), ('sync_products', '=', True)])
        for config in configs:
            try:
                config.action_sync_all_products()
            except Exception:
                _logger.exception(
                    'Product sync cron failed for Square config %s',
                    config.id,
                )

    @api.model
    def _cron_sync_inventory_to_square(self):
        configs = self.search([('active', '=', True), ('sync_inventory', '=', True)])
        for config in configs:
            try:
                config._sync_inventory_to_square()
            except Exception:
                _logger.exception(
                    'Inventory sync cron failed for Square config %s',
                    config.id,
                )

    def _sync_inventory_to_square(self):
        """Push Odoo stock quantities to Square per mapped location."""
        self.ensure_one()
        self._ensure_oauth_scopes(SQUARE_OAUTH_SCOPES_INVENTORY)
        client = self._get_api_client()
        locations = self.location_ids.filtered(
            lambda loc: loc.active and loc.stock_location_id and loc.square_location_id
        )
        if not locations:
            _logger.warning('No mapped Square locations for config %s', self.id)
            return

        changes = []
        for location in locations:
            products = self.env['product.product'].search([
                ('square_variation_id', '!=', False),
                ('type', '=', 'product'),
            ])
            for product in products:
                qty = product.with_context(
                    location=location.stock_location_id.id
                ).qty_available
                quantity_str = str(int(qty)) if qty == int(qty) else str(qty)
                changes.append({
                    'type': 'PHYSICAL_COUNT',
                    'physical_count': {
                        'catalog_object_id': product.square_variation_id,
                        'location_id': location.square_location_id,
                        'quantity': quantity_str,
                        'occurred_at': fields.Datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ'),
                        'state': 'IN_STOCK',
                    },
                })

        if not changes:
            return

        # Square batch limit is 100 changes per request
        batch_size = 100
        for idx in range(0, len(changes), batch_size):
            batch = changes[idx:idx + batch_size]
            idempotency_key = str(uuid.uuid4())
            client.post('/v2/inventory/changes/batch-create', {
                'idempotency_key': idempotency_key,
                'changes': batch,
            })
        self.write({
            'last_sync_at': fields.Datetime.now(),
            'last_sync_status': 'success',
            'last_sync_message': _('Inventory pushed: %s change(s).') % len(changes),
        })
        _logger.info(
            'Pushed %s inventory changes to Square for config %s',
            len(changes),
            self.id,
        )

    @api.model
    def get_active_config(self, company=None):
        company = company or self.env.company
        config = self.search([('company_id', '=', company.id), ('active', '=', True)], limit=1)
        if not config:
            raise UserError(_('No active Square configuration found for this company.'))
        return config

    def _oauth_return_redirect_url(self, error_message=None):
        """Redirect back to the Square configuration form after OAuth."""
        self.ensure_one()
        action = self.env.ref('odoo_square_all_in_one.action_square_config')
        menu = self.env.ref('odoo_square_all_in_one.menu_square_config', raise_if_not_found=False)
        url = f'/web#id={self.id}&model=square.config&view_type=form'
        if action:
            url += f'&action={action.id}'
        if menu:
            url += f'&menu_id={menu.id}'
        if error_message:
            url += f'&square_oauth_error={error_message[:200]}'
        else:
            url += '&square_oauth=connected'
        return url
