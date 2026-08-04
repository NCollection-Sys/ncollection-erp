# -*- coding: utf-8 -*-
"""The zero-financial-computation boundary test (F3-T01 acceptance).

FPA §7: ncollection_account_dashboard "Must Never Own: Report Generation,
Accounting Rules". This test enforces that in code, not just docs — it walks the
AST of every python file in the module and fails if any line actually ACCESSES
accounting data or runs aggregation:

  * self.env['account.move'] / ['account.move.line']   (direct move access)
  * cr.execute(...) / any *.execute(...) SQL
  * read_group / _read_group / search_read              (aggregation)

Docstrings and comments that merely NAME these (this module's own do) are AST
nodes we never inspect, so they don't trip it — only real access does. The
companion provenance test (test_dashboard_service) proves the positive side:
every figure equals the executive-service output.
"""
import ast
import os

from odoo.tests import TransactionCase, tagged

_FORBIDDEN_MODELS = {'account.move', 'account.move.line'}
_FORBIDDEN_CALLS = {'execute', 'read_group', '_read_group', 'search_read'}
_MODULE_DIR = os.path.dirname(os.path.dirname(__file__))


def _python_files(root):
    for dirpath, _dirs, files in os.walk(root):
        # Skip the tests package itself — it legitimately posts account.move to
        # build a fixture; the boundary is about the runtime module code.
        if os.path.basename(dirpath) == 'tests':
            continue
        for name in files:
            if name.endswith('.py'):
                yield os.path.join(dirpath, name)


@tagged('post_install', '-at_install')
class TestZeroComputationBoundary(TransactionCase):

    def test_no_accounting_data_access(self):
        offenders = []
        for path in _python_files(_MODULE_DIR):
            with open(path, encoding='utf-8') as fh:
                tree = ast.parse(fh.read(), filename=path)
            rel = os.path.relpath(path, _MODULE_DIR)
            for node in ast.walk(tree):
                # self.env['account.move' / 'account.move.line']
                if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) \
                        and node.value.attr == 'env':
                    key = node.slice
                    if isinstance(key, ast.Constant) and key.value in _FORBIDDEN_MODELS:
                        offenders.append("%s:%s env[%r]" % (rel, node.lineno, key.value))
                # *.execute(...) / *.read_group(...) / *.search_read(...)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                        and node.func.attr in _FORBIDDEN_CALLS:
                    offenders.append("%s:%s .%s(...)" % (rel, node.lineno, node.func.attr))
        self.assertFalse(
            offenders,
            "ncollection_account_dashboard must do ZERO financial computation "
            "(FPA §7). Forbidden accounting-data access found:\n  " + "\n  ".join(offenders))

    def test_depends_on_the_service_layer(self):
        # The module must declare its dependency on the report SERVICES it
        # consumes — the whole point of the boundary.
        manifest_path = os.path.join(_MODULE_DIR, '__manifest__.py')
        with open(manifest_path, encoding='utf-8') as fh:
            manifest = ast.literal_eval(fh.read())
        self.assertIn('ncollection_account_reports', manifest['depends'])
