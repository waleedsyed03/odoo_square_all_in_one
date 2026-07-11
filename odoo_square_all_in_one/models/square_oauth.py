# -*- coding: utf-8 -*-
"""Square OAuth helpers compatible with the SHC4WC Cloudflare Worker."""

import base64
import hashlib
import hmac
import logging
import re
import time
from urllib.parse import urlencode

import requests

from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

DEFAULT_OAUTH_WORKER_BASE = 'https://shc4wc-square-oauth.wordpress-ingenious.workers.dev'
OAUTH_STATE_VERSION = 'v1'
OAUTH_STATE_MAX_AGE = 1200
SESSION_ID_RE = re.compile(r'^[a-zA-Z0-9_-]{1,128}$')

# Scopes required for Odoo Square All-in-One (must match worker authorize URL).
SQUARE_OAUTH_SCOPES_CATALOG = ('ITEMS_READ', 'ITEMS_WRITE')
SQUARE_OAUTH_SCOPES_INVENTORY = ('INVENTORY_READ', 'INVENTORY_WRITE')
SQUARE_OAUTH_SCOPES_PAYMENTS = (
    'ORDERS_READ',
    'ORDERS_WRITE',
    'PAYMENTS_READ',
    'PAYMENTS_WRITE',
    'MERCHANT_PROFILE_READ',
    'CUSTOMERS_READ',
)
SQUARE_OAUTH_SCOPES_ALL = (
    SQUARE_OAUTH_SCOPES_CATALOG
    + SQUARE_OAUTH_SCOPES_INVENTORY
    + SQUARE_OAUTH_SCOPES_PAYMENTS
)


class SquareOAuthHelper:
    """Client for the existing SHC4WC Square OAuth Cloudflare Worker."""

    def __init__(self, env, worker_base=None, registration_secret=None):
        self.env = env
        self.worker_base = (worker_base or DEFAULT_OAUTH_WORKER_BASE).rstrip('/')
        self.registration_secret = (registration_secret or '').strip()

    # -------------------------------------------------------------------------
    # Signed return state (mirrors WordPress SHC4WC_OAuth_Return_State)
    # -------------------------------------------------------------------------

    def _return_secret(self):
        icp = self.env['ir.config_parameter'].sudo()
        secret = icp.get_param('odoo_square.oauth.return_secret')
        if not secret:
            secret = hashlib.sha256(str(time.time()).encode()).hexdigest()
            icp.set_param('odoo_square.oauth.return_secret', secret)
        return f'{secret}|square_oauth_return'

    def create_return_state(self, config_id):
        ts = int(time.time())
        rand = hashlib.sha256(f'{time.time_ns()}-{config_id}'.encode()).hexdigest()[:16]
        payload = f'{OAUTH_STATE_VERSION}|{ts}|{rand}|{config_id}'
        sig = hmac.new(
            self._return_secret().encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip('=')
        return f'{encoded}.{sig}'

    def verify_return_state(self, state, config_id=None):
        state = (state or '').strip()
        if '.' not in state:
            return False
        encoded, sig = state.split('.', 1)
        pad = '=' * (-len(encoded) % 4)
        try:
            payload = base64.urlsafe_b64decode(encoded + pad).decode()
        except (ValueError, UnicodeDecodeError):
            return False

        expected = hmac.new(
            self._return_secret().encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return False

        parts = payload.split('|')
        if len(parts) != 4 or parts[0] != OAUTH_STATE_VERSION:
            return False

        ts = int(parts[1])
        if ts <= 0 or abs(time.time() - ts) > OAUTH_STATE_MAX_AGE:
            return False

        state_config_id = int(parts[3])
        if config_id is not None and state_config_id != int(config_id):
            return False
        return True

    @staticmethod
    def is_valid_session_id(session_id):
        return bool(session_id and SESSION_ID_RE.match(session_id))

    # -------------------------------------------------------------------------
    # Site URL normalization (compatible with worker expectations)
    # -------------------------------------------------------------------------

    @staticmethod
    def normalize_site_url(url):
        url = (url or '').strip()
        if not url:
            return url
        if not url.endswith('/'):
            url += '/'
        return url

    @staticmethod
    def worker_env(environment):
        return 'live' if environment == 'production' else 'sandbox'

    # -------------------------------------------------------------------------
    # Worker API
    # -------------------------------------------------------------------------

    def register_site(self, site_url):
        headers = {'Content-Type': 'application/json; charset=utf-8'}
        if self.registration_secret:
            headers['X-SHC4WC-Registration-Secret'] = self.registration_secret

        try:
            response = requests.post(
                f'{self.worker_base}/register',
                headers=headers,
                json={'site_url': self.normalize_site_url(site_url)},
                timeout=45,
            )
        except requests.RequestException as exc:
            _logger.exception('Square OAuth worker /register failed')
            raise UserError(_('Could not reach the Square OAuth service: %s') % exc) from exc

        data = response.json() if response.text else {}
        if response.status_code >= 400 or not data.get('site_key'):
            error = data.get('error') or response.text or _('Registration failed')
            raise UserError(_('Square OAuth registration failed: %s') % error)

        return data['site_key']

    def build_start_url(self, site_key, return_url, environment):
        env = self.worker_env(environment)
        ts = int(time.time())
        canonical = f'{return_url}\n{env}\n{ts}'
        sig = hmac.new(site_key.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        query = urlencode({
            'return_url': return_url,
            'site_key': site_key,
            'env': env,
            'ts': str(ts),
            'sig': sig,
        })
        return f'{self.worker_base}/start?{query}'

    def claim_session(self, site_key, session_id, site_url):
        try:
            response = requests.post(
                f'{self.worker_base}/claim',
                headers={'Content-Type': 'application/json; charset=utf-8'},
                json={
                    'site_key': site_key,
                    'session_id': session_id,
                    'site_url': self.normalize_site_url(site_url),
                },
                timeout=60,
            )
        except requests.RequestException as exc:
            _logger.exception('Square OAuth worker /claim failed')
            raise UserError(_('Square OAuth claim failed: %s') % exc) from exc

        data = response.json() if response.text else {}
        if response.status_code >= 400 or not data.get('access_token'):
            error = data.get('error') or response.text or _('Claim failed')
            raise UserError(_('Could not complete Square connection: %s') % error)
        return data

    def refresh_token(self, site_key, refresh_token, environment):
        env = self.worker_env(environment)
        ts = int(time.time())
        sig = hmac.new(
            site_key.encode(),
            f'{refresh_token}\n{env}\n{ts}'.encode(),
            hashlib.sha256,
        ).hexdigest()
        try:
            response = requests.post(
                f'{self.worker_base}/refresh',
                headers={'Content-Type': 'application/json; charset=utf-8'},
                json={
                    'site_key': site_key,
                    'refresh_token': refresh_token,
                    'env': env,
                    'ts': ts,
                    'sig': sig,
                },
                timeout=60,
            )
        except requests.RequestException as exc:
            _logger.exception('Square OAuth worker /refresh failed')
            raise UserError(_('Square token refresh failed: %s') % exc) from exc

        data = response.json() if response.text else {}
        if response.status_code >= 400 or not data.get('access_token'):
            error = data.get('error') or response.text or _('Refresh failed')
            raise UserError(_('Square token refresh failed: %s') % error)
        return data

    def revoke_token(self, site_key, access_token, environment):
        env = self.worker_env(environment)
        ts = int(time.time())
        sig = hmac.new(
            site_key.encode(),
            f'{access_token}\n{env}\n{ts}'.encode(),
            hashlib.sha256,
        ).hexdigest()
        try:
            requests.post(
                f'{self.worker_base}/revoke',
                headers={'Content-Type': 'application/json; charset=utf-8'},
                json={
                    'site_key': site_key,
                    'access_token': access_token,
                    'env': env,
                    'ts': ts,
                    'sig': sig,
                },
                timeout=30,
            )
        except requests.RequestException:
            _logger.warning('Square OAuth revoke request failed (non-fatal)')
