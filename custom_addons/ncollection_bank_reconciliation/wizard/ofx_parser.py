"""OFX import (Open Financial Exchange 1.x SGML and 2.x XML), dependency-free.

Why not OCA: ``account_statement_import_ofx`` has no 19.0 release (the migration is an open, unmerged pull request,
OCA/bank-statement-import#924) and it needs the ``ofxparse`` library, which the ``odoo:19`` image does not ship
(verified). Pinning an unmerged branch plus changing the image is a larger risk than the format: the banking subset of
OFX is a flat list of <STMTTRN> records inside <STMTRS> / <CCSTMTRS>, read here with a tolerant tag reader that handles
both the SGML form (closing tags optional) and the XML form.

Strict by design: a statement without CURDEF / ACCTID, or a transaction without DTPOSTED, TRNAMT or FITID, rejects the
WHOLE file with the transaction's position (never a partial import). FITID is the bank's transaction id and becomes the
duplicate key. Investment / loan statements are refused, not skipped.
"""
import re
from datetime import datetime

from odoo import models
from odoo.exceptions import UserError

_TAG = re.compile(r'<(/?)([A-Za-z0-9.]+)>([^<]*)')


def _looks_like_ofx(data):
    head = data[:2048].lstrip()
    return head.startswith(b'OFXHEADER') or b'<OFX>' in data[:4096].upper(
    ) or head.startswith(b'<?xml') and b'<OFX' in data[:4096]


def _blocks(text, name):
    """Raw text of every <name>...</name> aggregate (aggregates are always closed, in SGML too)."""
    return re.findall(r'<%s>(.*?)</%s>' % (name, name), text, flags=re.S | re.I)


def _leaf(block, name):
    match = re.search(r'<%s>([^<\r\n]*)' % name, block, flags=re.I)
    return match.group(1).strip() if match else ''


def _date(value):
    digits = re.match(r'(\d{8})', value or '')
    if not digits:
        return None
    return datetime.strptime(digits.group(1), '%Y%m%d').date()


def _amount(value):
    value = (value or '').strip().replace(',', '.')
    if not re.fullmatch(r'[+-]?\d+(\.\d+)?', value):
        return None
    return float(value)


class AccountStatementImport(models.TransientModel):
    _inherit = 'account.statement.import'

    def _parse_file(self, data_file):
        if not _looks_like_ofx(data_file):
            return super()._parse_file(data_file)
        return self._nc_parse_ofx(data_file)

    def _nc_parse_ofx(self, data_file):
        try:
            text = data_file.decode('utf-8')
        except UnicodeDecodeError:
            text = data_file.decode('latin-1')
        if _blocks(text, 'INVSTMTRS') or _blocks(text, 'LOANSTMTRS'):
            raise UserError(self.env._("This OFX file is an investment or loan statement; only bank and credit card "
                                       "statements can be imported."))
        statements = _blocks(text, 'STMTRS') + _blocks(text, 'CCSTMTRS')
        if not statements:
            raise UserError(self.env._("This OFX file contains no bank or credit card statement."))
        result = []
        for index, stmt in enumerate(statements, start=1):
            currency = _leaf(stmt, 'CURDEF')
            account = _leaf(stmt, 'ACCTID')
            if not currency or not account:
                raise UserError(self.env._(
                    "OFX statement %(index)s has no currency (CURDEF) or account number (ACCTID). Nothing was "
                    "imported.", index=index))
            transactions = []
            for position, trn in enumerate(_blocks(stmt, 'STMTTRN'), start=1):
                date, amount, fitid = _date(_leaf(trn, 'DTPOSTED')), _amount(_leaf(trn, 'TRNAMT')), _leaf(trn, 'FITID')
                if date is None or amount is None or not fitid:
                    raise UserError(self.env._(
                        "OFX transaction %(position)s of statement %(index)s is missing its date (DTPOSTED), amount "
                        "(TRNAMT) or bank id (FITID), or one of them is malformed. Nothing was imported.",
                        position=position, index=index))
                name, memo = _leaf(trn, 'NAME'), _leaf(trn, 'MEMO')
                vals = {
                    'date': date, 'amount': amount, 'unique_import_id': fitid,
                    'payment_ref': name or memo or _leaf(trn, 'TRNTYPE') or fitid,
                    'ref': _leaf(trn, 'CHECKNUM') or _leaf(trn, 'REFNUM') or False,
                    'narration': memo if name and memo and memo != name else False,
                }
                if name:
                    vals['partner_name'] = name
                transactions.append(vals)
            ledger = _blocks(stmt, 'LEDGERBAL')
            st_vals = {'name': '%s %s' % (account, _leaf(stmt, 'DTEND')[:8] or ''), 'transactions': transactions}
            if transactions:
                st_vals['date'] = max(t['date'] for t in transactions)
            balance_end = _amount(_leaf(ledger[0], 'BALAMT')) if ledger else None
            if balance_end is not None:
                st_vals['balance_end_real'] = balance_end
                st_vals['balance_start'] = round(balance_end - sum(t['amount'] for t in transactions), 2)
            result.append((currency.upper(), account, [st_vals]))
        return result
