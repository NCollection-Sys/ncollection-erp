# -*- coding: utf-8 -*-
"""Every NCollection client action must be listed in the shared scroll contract.

ROOT CAUSE GUARDED. Odoo's ``.o_action_manager`` is a fixed-height box with
``overflow: hidden``. Native views scroll because Odoo wraps them in
``.o_action``/``.o_content``; our OWL client actions are mounted DIRECTLY in the
manager. Each used to declare ``overflow-y: auto`` with ``min-height: 100%`` and
no height, so the component grew to its content, its own overflow never engaged,
and the manager silently clipped the bottom of Home, the Reports hub, every
report and the dashboards.

``static/src/scss/app_shell.scss`` gives every listed root ``block-size: 100%``
and ``overflow-y: auto``. This test walks the registered client actions,
resolves each one's template root element (following ``extends`` for components
that inherit their template) and fails when a root class is missing from that
list — so a NEW page cannot ship with the same bug.
"""
import glob
import os
import re

from odoo.tests import TransactionCase, tagged

_ADDONS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SHELL = os.path.join(_ADDONS, 'ncollection_branding', 'static', 'src', 'scss', 'app_shell.scss')


def _read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def _listed_roots():
    text = _read(_SHELL)
    block = re.search(r'\.o_action_manager\s*>\s*:is\((.*?)\)\s*\{', text, re.DOTALL)
    assert block, 'app_shell.scss lost its client-action root list'
    return set(re.findall(r'\.([A-Za-z0-9_-]+)', block.group(1)))


def _client_action_roots():
    """{js file: set of root classes} for every JS file registering an action."""
    sources = {}
    for path in glob.glob(os.path.join(_ADDONS, 'ncollection_*', 'static', 'src', '**', '*.js'),
                          recursive=True):
        sources[path] = _read(path)
    classes = {}                      # class name -> (template, parent)
    for text in sources.values():
        for match in re.finditer(
                r'class\s+(\w+)\s+extends\s+([\w.]+)\s*\{(.*?)\n\}', text, re.DOTALL):
            name, parent, body = match.groups()
            template = re.search(r'static template\s*=\s*"([^"]+)"', body)
            classes[name] = (template.group(1) if template else None, parent)
    templates = {}
    for path in glob.glob(os.path.join(_ADDONS, 'ncollection_*', 'static', 'src', '**', '*.xml'),
                          recursive=True):
        text = _read(path)
        for match in re.finditer(r't-name="([^"]+)"', text):
            root = re.search(r'<(?!t[\s>/])\w+[^>]*class="([^"]+)"', text[match.end():])
            templates[match.group(1)] = set(root.group(1).split()) if root else set()
    roots = {}
    for path, text in sources.items():
        for match in re.finditer(r'category\("actions"\)\.add\("[^"]+",\s*(\w+)\)', text):
            name, seen = match.group(1), set()
            template = None
            while name in classes and name not in seen:
                seen.add(name)
                template, parent = classes[name]
                if template:
                    break
                name = parent
            roots[(os.path.relpath(path, _ADDONS), match.group(1))] = templates.get(template, set())
    return roots


@tagged('post_install', '-at_install')
class TestAppShellScroll(TransactionCase):

    def test_every_client_action_root_is_in_the_shared_scroll_contract(self):
        listed = _listed_roots()
        roots = _client_action_roots()
        self.assertTrue(roots, 'no client actions found - the scan itself is broken')
        missing = [
            '%s (%s): root classes %s' % (path, cls, sorted(classes) or '?')
            for (path, cls), classes in sorted(roots.items())
            if not classes & listed
        ]
        self.assertFalse(
            missing,
            "These client actions are mounted straight into .o_action_manager "
            "(overflow:hidden) but are not listed in "
            "ncollection_branding/static/src/scss/app_shell.scss, so the bottom "
            "of the page is clipped and unreachable:\n" + '\n'.join(missing))

    def test_the_contract_gives_the_roots_a_height_and_their_own_scroll(self):
        text = _read(_SHELL)
        self.assertRegex(text, r'block-size:\s*100%')
        self.assertRegex(text, r'overflow-y:\s*auto')

    def test_the_contract_is_in_the_backend_bundle(self):
        manifest = _read(os.path.join(_ADDONS, 'ncollection_branding', '__manifest__.py'))
        self.assertIn('static/src/scss/app_shell.scss', manifest)
