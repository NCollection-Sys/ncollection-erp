"""Arabic for the Python messages of the OCA bank modules, without editing the pinned OCA source.

Odoo 19 looks a Python message up under the module that raised it (``odoo.tools.translate.CodeTranslations``) and
reads that module's own ``i18n/<lang>.po`` only, so a translation shipped by another module never reaches it.
``account_reconcile_oca`` ships no Arabic at all. The view / field / menu / JS terms are translated through our own
``i18n/ar.po`` (they are looked up by XML id or globally); the Python messages are the one gap.

This adds, for the OCA modules listed below ONLY, the entries of ``i18n_oca/<module>/<lang>.po`` shipped here, read
with Odoo's own reader and filter (only ``#. odoo-python`` entries with a value). It never overrides a translation
the module itself provides, and it is inert for every other module and language. It does not change how any message
is raised or what it says in English.
"""
import logging
import os

from odoo.tools import translate

_logger = logging.getLogger(__name__)

OCA_MODULES = (
    'account_reconcile_oca',
    'account_statement_base',
    'account_statement_import_base',
    'account_statement_import_file',
    'account_statement_import_file_reconcile_oca',
    'account_statement_import_camt',
    'account_statement_import_sheet_file',
    'account_statement_import_sheet_file_xlsx',
    'account_statement_import_qif',
)
_HERE = os.path.dirname(os.path.abspath(__file__))


def _extra_paths(module_name, lang):
    base = (lang or '').split('_')[0]
    for name in dict.fromkeys((base, lang)):
        path = os.path.join(_HERE, 'i18n_oca', module_name, '%s.po' % name)
        if name and os.path.isfile(path):
            yield path


def _extra_python_translations(module_name, lang):
    def filter_func(row):
        return row.get('value') and translate.PYTHON_TRANSLATION_COMMENT in row['comments']
    found = {}
    for path in _extra_paths(module_name, lang):
        try:
            with open(path, 'rb') as fileobj:
                found.update(translate.CodeTranslations._read_code_translations_file(fileobj, filter_func))
        except (OSError, ValueError):
            _logger.exception('ncollection_bank_reconciliation: cannot read %s', path)
    return found


def install():
    original = translate.CodeTranslations._load_python_translations
    if getattr(original, '_nc_bank_wrapped', False):
        return

    def _load_python_translations(self, module_name, lang):
        original(self, module_name, lang)
        if module_name not in OCA_MODULES:
            return
        extra = _extra_python_translations(module_name, lang)
        if extra:
            merged = dict(extra)
            merged.update({k: v for k, v in self.python_translations[(module_name, lang)].items() if v})
            self.python_translations[(module_name, lang)] = translate.ReadonlyDict(merged)

    _load_python_translations._nc_bank_wrapped = True
    translate.CodeTranslations._load_python_translations = _load_python_translations
