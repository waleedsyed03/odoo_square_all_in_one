# -*- coding: utf-8 -*-
{
    'name': 'Square Sync & Pay: Inventory, Google Pay, Apple Pay+',
    'version': '18.0.2.1.5',
    'category': 'Accounting/Payment Providers',
    'sequence': 360,
    'summary': 'Sync catalog & inventory with Square. Hosted checkout with Google Pay, Apple Pay, Cash App & Afterpay.',
    'description': """
Square Sync & Pay for Odoo 18 (Free)
====================================

Connect Square to Odoo with one click - no monthly fee for this module.

Features
--------
* **Connect with Square** - secure OAuth (sandbox & production)
* **Catalog sync** - Odoo <-> Square products (push & import)
* **Inventory sync** - multi-location stock updates (push, import & webhooks)
* **Payment gateway** - Square Hosted Checkout on Website Sales
* **Digital wallets** - Google Pay, Apple Pay, Cash App Pay & Afterpay (via Square)
* **POS** - Square Terminal API payment method
* **24/7 WhatsApp support** built into the app

Requirements
------------
* Odoo 18 with Inventory, Sales, Website eCommerce, Point of Sale, and Payment apps
* A Square seller account (sandbox or live)
* Python package: requests

This module is **100% free** and open source (LGPL-3).
    """,
    'author': 'WPPayments',
    'website': 'https://payments-connect-square.pages.dev/',
    'support': 'xayed.waleed@gmail.com',
    'maintainer': 'WPPayments',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'product',
        'stock',
        'payment',
        'point_of_sale',
        'website_sale',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/cron_data.xml',
        'data/payment_provider_data.xml',
        'data/payment_provider_sync.xml',
        'views/square_location_views.xml',
        'views/square_support_views.xml',
        'views/square_config_views.xml',
        'views/payment_provider_views.xml',
        'views/pos_payment_views.xml',
        'views/product_template_views.xml',
        'views/menu_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'odoo_square_all_in_one/static/src/js/pos_square_terminal.js',
        ],
    },
    'external_dependencies': {
        'python': ['requests'],
    },
    'images': [
        'static/description/hero_banner.png',
        'static/description/catalog_sync.png',
        'static/description/inventory.png',
        'static/description/ecommerce_checkout.png',
        'static/description/pos_terminal.png',
        'static/description/whatsapp_support.png',
        'static/description/scan.png',
        'static/description/free_open_source.png',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
}
