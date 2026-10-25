# -*- coding: utf-8 -*-
"""Every user-facing string an NCollection module ships must have an Arabic value, reference by reference.

WHY THE EXISTING CHECK WAS NOT ENOUGH. ``test_translations`` proves each ``ar.po`` is well formed and has no empty
translation. It cannot see a string that was never put in the catalogue at all, and it cannot see the subtler failure:
Odoo applies a translation to a record only through the exact reference line on the entry (``#: model:ir.ui.menu,name:
<xmlid>``). A catalogue that already holds the sentence "Summary" for an action still leaves the *menu* called Summary
in English, and the Home card for it stays English in an Arabic session.

So this test asks Odoo's own extractor (the one behind ``odoo i18n export``) for every term of every installed
NCollection module and requires that each user-facing one is present in that module's ``ar.po`` with a non-empty value
under its own reference. A new menu, view text, OWL/JS string or ``_()`` message that is not translated fails here, in
the pull request that adds it, instead of being found by a customer in Arabic.

EVERY NCollection module is covered, the platform/operator modules (``ncollection_saas``,
``ncollection_subscription``) included: there is no module-level exemption.

WHAT IS EXEMPT, AND WHY (each item is a decision, not a convenience):
  * technical text: colours, e-mail addresses, URLs, identifiers, model names, group comments, developer help, and
    strings that exist only in test code.
  * a small named list of operator/contract messages and brand names below.
"""
import ast
import glob
import os
import re

from odoo.tests import TransactionCase, tagged
from odoo.tools.translate import TranslationModuleReader

_ADDONS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# model terms that are read by people (labels, names, selections, mail), keyed 'model,field'
_USER_FACING_MODEL_TERMS = (
    'ir.ui.menu,name', 'ir.actions.act_window,name', 'ir.actions.client,name', 'ir.actions.server,name',
    'ir.model.fields,field_description', 'ir.model.fields.selection,name', 'mail.template,subject',
    'mail.template,body_html',
)

# Named exemptions: (module, regex on the source text). Operator diagnostics, contract errors, brand names.
_EXEMPT = (
    ('ncollection_auth', r'^(Auth-log retention|%\(param\)s |%\(sk\)s )'),            # operator purge diagnostics
    ('ncollection_core', r'^(No workspace configuration exists|Refusing to sync|sync_from_platform)'),  # sync contract
    ('ncollection_account_reports', r'^OCA$'),                                           # a brand name
    ('ncollection_api', r'^Core API key id$'),                                           # developer field label
)

_TECHNICAL = (
    re.compile(r'^#[0-9A-Fa-f]{3,8}$'),                       # a colour value
    re.compile(r'^\S+@\S+$'),                                 # an address
    re.compile(r'^https?://'),                                # a URL
    re.compile(r'^[a-z_][a-z0-9_.]*$'),                       # an identifier / dotted model or field path
    re.compile(r'^[a-z_.0-9]+(,[a-z_.0-9]+)*( or \*)?$'),     # a list of identifiers (event / scope names)
)

# Python-built HTML that is deliberately NOT translated, with the reason. Keyed by path relative to the addons dir.
_BARE_HTML_OK = {
    'ncollection_branding/models/vendor_content.py':
        "rewrite PATTERNS applied to the stored HTML of module mail templates; shown to no user as written",
    'ncollection_core/controllers/platform_entry.py':
        "the pre-login token bridge: auth-less, no user language is known, and it must reveal nothing",
    'ncollection_core/release_gate.py':
        "the maintenance page is served BEFORE any registry exists (no catalogue, no user language): it carries its "
        "own English and Arabic text, side by side",
}

_BS = chr(92)


def _unescape(raw):
    return raw.replace(_BS + 'n', '\n').replace(_BS + '"', '"').replace(_BS * 2, _BS)


def _parse_po(path):
    """{msgid: (msgstr, {refs})} with continuation lines joined and escapes undone."""
    with open(path, encoding='utf-8') as fh:
        text = fh.read().replace('\r\n', '\n')
    entries = {}
    for block in re.split(r'\n\s*\n', text):
        msgid, msgstr, key, refs = '', '', None, set()
        for line in (ln for ln in block.split('\n') if ln.strip()):
            if line.startswith('msgid '):
                msgid, key = line[6:].strip()[1:-1], 'id'
            elif line.startswith('msgstr'):
                msgstr, key = line[6:].strip()[1:-1], 'str'
            elif line.startswith('"'):
                if key == 'id':
                    msgid += line.strip()[1:-1]
                elif key == 'str':
                    msgstr += line.strip()[1:-1]
            elif line.startswith('#:'):
                refs.update(line[2:].split())
        if msgid:
            old = entries.get(_unescape(msgid))
            value = _unescape(msgstr) or (old[0] if old else '')
            entries[_unescape(msgid)] = (value, refs | (old[1] if old else set()))
    return entries


def _squash(text):
    """Odoo compares view/QWeb terms with whitespace collapsed."""
    return ' '.join(text.split())


@tagged('post_install', '-at_install')
class TestTranslationCoverage(TransactionCase):

    def _modules(self):
        modules = self.env['ir.module.module'].search(
            [('name', '=like', 'ncollection\\_%'), ('state', '=', 'installed')]).mapped('name')
        return sorted(modules)

    @staticmethod
    def _technical(source):
        text = source.strip()
        return any(pattern.match(text) for pattern in _TECHNICAL)

    def _exempt(self, module, source):
        return any(m == module and re.match(rx, source) for m, rx in _EXEMPT)

    def _wanted(self, row):
        """(ref or None, source) for a row the rule applies to, else None."""
        kind, name, res_id, source = row[1], row[2], row[3], row[4]
        if not source or not source.strip():
            return None
        if kind == 'code':
            if '/tests/' in name or not (name.endswith('.py') or name.endswith('.js') or name.endswith('.xml')):
                return None
            return None, source
        if kind == 'model' and name in _USER_FACING_MODEL_TERMS:
            return 'model:%s:%s' % (name, res_id), source
        if kind == 'model_terms' and name in ('ir.ui.view,arch_db', 'ir.actions.act_window,help'):
            return 'model_terms:%s:%s' % (name, res_id), source
        return None

    def test_every_user_facing_term_has_an_arabic_value_under_its_own_reference(self):
        missing = []
        for module in self._modules():
            po = os.path.join(_ADDONS, module, 'i18n', 'ar.po')
            catalogue = _parse_po(po) if os.path.exists(po) else None
            by_squashed = {}
            for source, (value, refs) in (catalogue or {}).items():
                kept = by_squashed.get(_squash(source), ('', set()))
                by_squashed[_squash(source)] = (value or kept[0], refs | kept[1])
            for row in TranslationModuleReader(self.env.cr, modules=[module]):
                wanted = self._wanted(row)
                if wanted is None:
                    continue
                ref, source = wanted
                if self._technical(source) or self._exempt(module, source):
                    continue
                entry = by_squashed.get(_squash(source))
                if catalogue is None:
                    missing.append('%s has no i18n/ar.po but ships %r' % (module, source[:60]))
                    break
                if not entry or not entry[0]:
                    missing.append('%s: no Arabic value for %r' % (module, source[:70]))
                elif ref and ref not in entry[1]:
                    missing.append('%s: %r is translated, but not for %s' % (module, source[:50], ref))
        self.assertFalse(
            missing,
            "User-facing NCollection text with no Arabic value (add it to the module's i18n/ar.po, or, if it is "
            "technical, exempt it in this test with the reason):\n  " + "\n  ".join(missing[:60]))

    def test_every_module_that_ships_user_facing_text_has_an_arabic_catalogue(self):
        absent = []
        for module in self._modules():
            if os.path.exists(os.path.join(_ADDONS, module, 'i18n', 'ar.po')):
                continue
            for row in TranslationModuleReader(self.env.cr, modules=[module]):
                wanted = self._wanted(row)
                if wanted and not self._technical(wanted[1]):
                    absent.append(module)
                    break
        self.assertFalse(absent, "no i18n/ar.po but user-facing text: %s" % absent)

    def test_no_python_builds_ui_html_from_bare_english(self):
        """A message built into HTML inside Python is invisible to the extractor unless it goes through ``_()`` /
        ``self.env._()``: no catalogue entry can ever translate it. The storage table on the platform Summary, the
        anomaly digest e-mail and the import validation result were all like that. This finds the pattern itself, in
        every NCollection module, instead of waiting for someone to notice it in Arabic."""
        offenders = []
        pattern = os.path.join(_ADDONS, 'ncollection_*', '**', '*.py')
        for path in sorted(glob.glob(pattern, recursive=True)):
            rel = os.path.relpath(path, _ADDONS).replace(os.sep, '/')
            parts = rel.split('/')
            if (set(parts) & {'tests', 'migrations', 'scripts', 'static', '__pycache__'}) or rel in _BARE_HTML_OK:
                continue
            with open(path, encoding='utf-8') as fh:
                try:
                    tree = ast.parse(fh.read())
                except SyntaxError:
                    continue
            offenders.extend(self._bare_html_in(tree, rel))
        self.assertFalse(
            offenders,
            "HTML built from untranslated English in Python (wrap the words in self.env._(), or list the file in "
            "_BARE_HTML_OK with the reason):\n  " + "\n  ".join(offenders[:40]))

    @staticmethod
    def _bare_html_in(tree, rel):
        found = []

        def is_translation_call(node):
            func = node.func
            return ((isinstance(func, ast.Name) and func.id in ('_', '_lt'))
                    or (isinstance(func, ast.Attribute) and func.attr in ('_', '_lt')))

        def visit(node, guarded):
            if isinstance(node, ast.Call):
                if is_translation_call(node):
                    guarded = True
                elif isinstance(node.func, (ast.Name, ast.Attribute)) and getattr(
                        node.func, 'attr', getattr(node.func, 'id', '')) == 'route':
                    guarded = True                                   # URL patterns, not words
            if isinstance(node, ast.keyword) and node.arg in ('help', 'string', 'description', 'default'):
                guarded = True                                       # extracted by Odoo from the field declaration
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
                body = node.body
                first = body[0].value if body and isinstance(body[0], ast.Expr) else None
                if isinstance(first, ast.Constant):
                    first._is_docstring = True
            if (isinstance(node, ast.Constant) and isinstance(node.value, str) and not guarded
                    and not getattr(node, '_is_docstring', False)):
                text = node.value
                if re.search(r'<[A-Za-z/]', text):
                    # what a person would read: drop the tags (class names live inside them) and the placeholders
                    visible = re.sub(r'%\([^)]*\)[sd]|%[sdr]|\{[^}]*\}', ' ', re.sub(r'<[^>]*>', ' ', text))
                    if re.search(r'[A-Za-z]{4,}', visible):
                        found.append('%s:%s: %r' % (rel, node.lineno, text[:70]))
            for child in ast.iter_child_nodes(node):
                visit(child, guarded)

        visit(tree, False)
        return found

    def test_every_code_entry_carries_the_marker_odoo_loads_it_by(self):
        """Odoo loads a Python code translation only from an entry marked ``#. odoo-python`` and a JS / OWL one only
        from ``#. odoo-javascript`` (odoo/tools/translate.py, CodeTranslations). An entry that has the Arabic but
        not the marker passes the check above and is still shown in English: seven System Branding messages and the
        Summary breadcrumb were, until this test."""
        unmarked = []
        for module in self._modules():
            po = os.path.join(_ADDONS, module, 'i18n', 'ar.po')
            if not os.path.exists(po):
                continue
            with open(po, encoding='utf-8') as handle:
                text = handle.read().replace('\r\n', '\n')
            for block in re.split(r'\n\s*\n', text):
                refs = re.findall(r'^#: (.*)$', block, re.M)
                codes = [r for line in refs for r in line.split() if r.startswith('code:addons/')]
                if any(r.split(':')[1].endswith('.py') for r in codes) and '#. odoo-python' not in block:
                    unmarked.append('%s (python): %s' % (module, block.split('msgid', 1)[-1][:60]))
                if any('/static/' in r for r in codes) and '#. odoo-javascript' not in block:
                    unmarked.append('%s (javascript): %s' % (module, block.split('msgid', 1)[-1][:60]))
        self.assertFalse(unmarked, 'Code entries Odoo will never load:\n  ' + '\n  '.join(unmarked[:40]))

    def test_the_platform_modules_are_held_to_the_same_rule(self):
        """The operator console is not exempt: both platform modules are in the set that is checked."""
        modules = self._modules()
        for name in ('ncollection_saas', 'ncollection_subscription'):
            self.assertIn(name, modules)
            self.assertTrue(os.path.exists(os.path.join(_ADDONS, name, 'i18n', 'ar.po')), name)
