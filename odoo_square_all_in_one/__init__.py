# -*- coding: utf-8 -*-

from . import models
from . import controllers

from odoo.addons.payment import setup_provider, reset_payment_provider


def post_init_hook(env):
    setup_provider(env, 'square')
    env['square.config'].sudo()._migrate_legacy_oauth_storage()


def uninstall_hook(env):
    reset_payment_provider(env, 'square')
