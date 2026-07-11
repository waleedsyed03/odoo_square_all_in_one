# -*- coding: utf-8 -*-

import logging

from odoo import http
from odoo.http import request

from ..models.square_oauth import SquareOAuthHelper

_logger = logging.getLogger(__name__)


class SquareOAuthController(http.Controller):
    """OAuth return handler for the SHC4WC Cloudflare Worker flow."""

    @http.route(
        '/square/oauth/return',
        type='http',
        auth='public',
        methods=['GET'],
        csrf=False,
        save_session=False,
    )
    def square_oauth_return(self, config_id=None, square_oauth_state=None, **kwargs):
        session_id = request.httprequest.args.get('shc4wc_session')
        config = request.env['square.config'].sudo()
        error_message = None

        try:
            if not config_id:
                raise ValueError('missing_config')
            config = config.browse(int(config_id))
            if not config.exists():
                raise ValueError('invalid_config')

            oauth = config._oauth_helper()
            if square_oauth_state and not oauth.verify_return_state(square_oauth_state, config.id):
                raise ValueError('invalid_state')
            if not SquareOAuthHelper.is_valid_session_id(session_id):
                raise ValueError('invalid_session')

            site_key = config.oauth_site_key or config._ensure_worker_registered()
            site_url = SquareOAuthHelper.normalize_site_url(config._get_site_base_url())
            data = oauth.claim_session(site_key, session_id, site_url)

            oauth_env = request.httprequest.args.get('square_oauth_env')
            if oauth_env in ('sandbox', 'production') and config.environment != oauth_env:
                config.write({'environment': oauth_env})

            config.apply_oauth_token_response(data)
            _logger.info('Square OAuth connected for config %s', config.id)
        except Exception as exc:
            _logger.exception('Square OAuth return failed')
            error_message = str(exc)
            if not config or not config.exists():
                return request.redirect('/web')

        return request.redirect(config._oauth_return_redirect_url(error_message))
