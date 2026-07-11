# -*- coding: utf-8 -*-
"""Migrate legacy single-token OAuth storage to per-environment buckets."""

import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    configs = env['square.config'].sudo().search([])
    if not configs:
        return
    try:
        env['square.config'].sudo()._migrate_legacy_oauth_storage()
        _logger.info('Square: migrated legacy OAuth storage for %s config(s).', len(configs))
    except Exception:
        _logger.exception('Square: legacy OAuth migration failed.')
