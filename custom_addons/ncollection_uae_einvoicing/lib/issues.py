# -*- coding: utf-8 -*-
"""A readiness / reconciliation finding, with its authority stated in its rule id.

``rule`` is ``OFFICIAL:<assert id>`` (a rule of the pinned PINT-AE Schematron) or ``NCOLLECTION:<name>`` (ours). The
message is an English template with ``%(name)s`` placeholders; the Odoo layer translates the template and fills
``params``, so the
library stays free of Odoo and the wording is translatable (ar.po)."""

from collections import namedtuple

ERROR = 'error'
WARNING = 'warning'

Issue = namedtuple('Issue', 'severity rule subject message params')


def make(rule, subject, message, params=None, severity=ERROR):
    if not rule.startswith(('OFFICIAL:', 'NCOLLECTION:')):
        raise ValueError('a rule id must say whose rule it is: %r' % rule)
    return Issue(severity, rule, subject, message, dict(params or {}))


def authority(issue):
    return issue.rule.split(':', 1)[0]


def errors(issues):
    return [issue for issue in issues if issue.severity == ERROR]
