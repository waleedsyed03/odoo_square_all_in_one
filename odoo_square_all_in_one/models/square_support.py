# -*- coding: utf-8 -*-

import re
from urllib.parse import quote

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.release import version as odoo_version

DEFAULT_SUPPORT_WHATSAPP = '923172125955'


class SquareSupportRequest(models.TransientModel):
    _name = 'square.support.request'
    _description = 'Square Support Request'

    name = fields.Char(string='Your Name', required=True)
    email = fields.Char(string='Your Email', required=True)
    issue_type = fields.Selection(
        selection=[
            ('connect', 'Square connection / OAuth'),
            ('ecommerce', 'eCommerce checkout'),
            ('pos', 'POS Terminal'),
            ('inventory', 'Product / inventory sync'),
            ('webhook', 'Webhooks'),
            ('other', 'Other technical issue'),
        ],
        string='Issue Type',
        required=True,
        default='other',
    )
    subject = fields.Char(string='Subject')
    message = fields.Text(
        string='Describe the Issue',
        required=True,
        help='What happened? What did you expect? Steps to reproduce…',
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        user = self.env.user
        if 'name' in fields_list and not res.get('name'):
            res['name'] = user.name
        if 'email' in fields_list and not res.get('email'):
            res['email'] = user.email or ''
        return res

    @api.model
    def get_whatsapp_number(self):
        number = self.env['ir.config_parameter'].sudo().get_param(
            'odoo_square.support_whatsapp',
            DEFAULT_SUPPORT_WHATSAPP,
        )
        return re.sub(r'\D+', '', number or DEFAULT_SUPPORT_WHATSAPP)

    def _get_issue_label(self):
        self.ensure_one()
        return dict(self._fields['issue_type'].selection).get(
            self.issue_type,
            _('Other'),
        )

    def _get_diagnostic_context(self):
        self.ensure_one()
        icp = self.env['ir.config_parameter'].sudo()
        base_url = icp.get_param('web.base.url', '')
        module = self.env['ir.module.module'].sudo().search(
            [('name', '=', 'odoo_square_all_in_one')],
            limit=1,
        )
        module_version = module.latest_version or module.installed_version or 'unknown'
        config = self.env['square.config'].sudo().search([
            ('company_id', '=', self.env.company.id),
            ('active', '=', True),
        ], limit=1)
        lines = [
            _('Site diagnostics') + ':',
            _('Odoo URL') + ': ' + (base_url or _('(not set)')),
            _('Odoo version') + ': ' + odoo_version,
            _('Module version') + ': ' + module_version,
            _('Company') + ': ' + self.env.company.display_name,
        ]
        if config:
            lines.extend([
                _('Square environment') + ': ' + (config.environment or 'unknown'),
                _('Square connected') + ': ' + ('yes' if config.oauth_connected else 'no'),
                _('Product sync') + ': ' + ('yes' if config.sync_products else 'no'),
                _('Inventory sync') + ': ' + ('yes' if config.sync_inventory else 'no'),
            ])
        else:
            lines.append(_('Square connected') + ': no')
        return '\n'.join(lines)

    def _build_whatsapp_text(self):
        self.ensure_one()
        subject = self.subject or self._get_issue_label()
        return '\n'.join([
            _('Square All-in-One — support request'),
            '',
            _('Name') + ': ' + (self.name or ''),
            _('Email') + ': ' + (self.email or ''),
            _('Issue type') + ': ' + self._get_issue_label(),
            _('Subject') + ': ' + subject,
            '',
            self.message or '',
            '',
            '---',
            self._get_diagnostic_context(),
        ])

    def action_open_whatsapp(self):
        self.ensure_one()
        if not self.name or not self.email or not self.message:
            raise UserError(_(
                'Please fill in your name, email, and describe the issue before opening WhatsApp.'
            ))
        number = self.get_whatsapp_number()
        if not number:
            raise UserError(_('Support WhatsApp number is not configured.'))
        url = 'https://wa.me/%s?text=%s' % (number, quote(self._build_whatsapp_text()))
        return {
            'type': 'ir.actions.act_url',
            'url': url,
            'target': 'new',
        }

    @api.model
    def action_open_support_form(self):
        """Open the support form (menu / config button)."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Support'),
            'res_model': 'square.support.request',
            'view_mode': 'form',
            'target': 'current',
        }
