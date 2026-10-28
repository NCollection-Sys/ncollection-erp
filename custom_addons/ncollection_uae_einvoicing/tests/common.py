# -*- coding: utf-8 -*-
"""Fixtures shared by the model tests: a UAE company on the real 'ae' chart, complete master data, a tax map, a
product."""

import base64
import hashlib
import secrets
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests import TransactionCase

from ..lib import validator

GROUP = 'ncollection_uae_einvoicing.'
# the shared secret between the engine and the (stubbed) validator worker in the tests: random per run, so no secret
# literal lives in the source
VALIDATOR_TOKEN = secrets.token_hex(24)


def signed_answer(answer, request, token=VALIDATOR_TOKEN, xml_sha256=None, nonce=None):
    """What a GENUINE worker answers to ``request`` (the decoded JSON body): the scripted fields plus the hash of the
    document it was given, the caller's nonce and the signature."""
    data = dict(answer)
    data['xml_sha256'] = xml_sha256 or hashlib.sha256(base64.b64decode(request['xml_b64'])).hexdigest()
    data['nonce'] = nonce or request['nonce']
    data['signature'] = validator.sign_answer(
        token,
        data['xml_sha256'],
        data['spec_version'],
        data['manifest_sha256'],
        data['engine'],
        data['failures'],
        data['nonce'],
    )
    return data


def validator_env(url):
    """The environment a test gives the engine to reach a stubbed worker."""
    return {'NC_EINVOICE_VALIDATOR_URL': url, 'NC_EINVOICE_VALIDATOR_TOKEN': VALIDATOR_TOKEN}


class EInvoiceCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # the licence mirror is tested on its own (test_hardening); every other test runs on a licensed workspace
        licence = patch.object(type(cls.env['ncollection.einvoice']), '_nc_licensed', lambda self: True)
        licence.start()
        cls.addClassCleanup(licence.stop)
        cls.ae = cls.env.ref('base.ae')
        cls.company = cls._company(
            'EInv Seller Co', tin='1987654321', trn='198765432102003', registry='112345678900003'
        )
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=cls.company.ids))
        cls.tax_s = cls._sale_tax('5% DU')
        cls.tax_ext = cls._sale_tax('0% EXT')
        cls.tax_z = cls._sale_tax('0%')
        Map = cls.env['ncollection.einvoice.tax.map']
        cls.map_s = Map.create({'company_id': cls.company.id, 'tax_id': cls.tax_s.id, 'pint_category': 'S'})
        cls.map_e = Map.create(
            {
                'company_id': cls.company.id,
                'tax_id': cls.tax_ext.id,
                'pint_category': 'E',
                'exemption_code': 'DL8.46.1',
            }
        )
        cls.map_z = Map.create({'company_id': cls.company.id, 'tax_id': cls.tax_z.id, 'pint_category': 'Z'})
        cls.config = cls.env['ncollection.einvoice.config'].create(
            {'company_id': cls.company.id, 'enabled': True, 'default_payment_means_code': '10'}
        )
        cls.buyer = cls._partner(
            'Noor Electronics LLC',
            tin='1345678901',
            trn='134567890123003',
            state='AZ',
            registry='112345679000001',
            city='Abu Dhabi',
        )
        cls.product = cls.env['product.product'].create(
            {
                'name': 'Widget',
                'list_price': 100.0,
                'nc_item_type': 'G',
                'nc_hs_code': '88098432324',
                'default_code': 'WID-1',
            }
        )

    # ---------------------------------------------------------------- builders
    @classmethod
    def _company(cls, name, tin=None, trn=None, registry=None):
        company = cls.env['res.company'].create({'name': name})
        company._nc_apply_uae_localization()
        state = cls.env['res.country.state'].search([('country_id', '=', cls.ae.id), ('code', '=', 'DU')], limit=1)
        company.partner_id.write(
            {
                'street': 'Al Maktoum Road',
                'city': 'Dubai',
                'state_id': state.id,
                'country_id': cls.ae.id,
                'nc_tin': tin,
                'nc_trn': trn,
                'company_registry': registry,
                'nc_legal_reg_type': 'TL',
                'nc_legal_reg_authority': 'Dubai Department of Economy',
            }
        )
        return company

    @classmethod
    def _partner(cls, name, tin=None, trn=None, state='AZ', registry=None, city='Abu Dhabi'):
        st = cls.env['res.country.state'].search([('country_id', '=', cls.ae.id), ('code', '=', state)], limit=1)
        return cls.env['res.partner'].create(
            {
                'name': name,
                'company_type': 'company',
                'street': 'Hamdan Street',
                'city': city,
                'state_id': st.id,
                'country_id': cls.ae.id,
                'nc_tin': tin,
                'nc_trn': trn,
                'company_registry': registry,
                'nc_legal_reg_type': 'TL' if registry else False,
                'nc_legal_reg_authority': 'DED' if registry else False,
            }
        )

    @classmethod
    def _sale_tax(cls, name, company=None):
        company = company or cls.company
        tax = cls.env['account.tax'].search(
            [('company_id', '=', company.id), ('type_tax_use', '=', 'sale'), ('name', '=', name)], limit=1
        )
        assert tax, 'tax %s not found on the AE chart' % name
        return tax

    def _invoice(self, lines=None, partner=None, move_type='out_invoice', post=True, **extra):
        lines = lines or [
            {
                'product_id': self.product.id,
                'quantity': 2,
                'price_unit': 100.0,
                'tax_ids': [Command.set(self.tax_s.ids)],
            }
        ]
        move = self.env['account.move'].create(
            dict(
                {
                    'move_type': move_type,
                    'partner_id': (partner or self.buyer).id,
                    'company_id': self.company.id,
                    'invoice_date': fields.Date.today(),
                    'invoice_date_due': fields.Date.add(fields.Date.today(), days=30),
                    'invoice_line_ids': [Command.create(line) for line in lines],
                },
                **extra
            )
        )
        if post:
            move.action_post()
        return move

    def _einvoice(self, move=None, **vals):
        move = move or self._invoice()
        return self.env['ncollection.einvoice'].create(
            dict({'move_id': move.id, 'company_id': self.company.id}, **vals)
        )

    @classmethod
    def _user(cls, login, groups, company=None):
        company = company or cls.company
        return (
            cls.env['res.users']
            .with_context(no_reset_password=True)
            .create(
                {
                    'name': login,
                    'login': login,
                    'email': login + '@example.com',
                    'company_id': company.id,
                    'company_ids': [Command.set(company.ids)],
                    'group_ids': [Command.set([cls.env.ref(g).id for g in groups])],
                }
            )
        )

    @staticmethod
    def _validated(record):
        """Put a record where only the official validation can: used to test what happens AFTER it."""
        record.sudo().write({'local_state': 'validated'})
        return record

    @staticmethod
    def rules(record):
        return {v.rule for v in record.validation_ids}
