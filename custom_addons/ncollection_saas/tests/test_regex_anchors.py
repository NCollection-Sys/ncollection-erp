# -*- coding: utf-8 -*-
"""Anchored validators must reject a trailing newline (#377).

WHY THIS FILE EXISTS. `$` does not mean end-of-string in Python — it matches at
the end OR immediately before a trailing newline. So

    re.compile(r'^[a-z]+$').match("postgres\\n")

returns a match. Every `^...$` + `.match()` validator in this repo accepted a
trailing newline; measured, eleven of them.

WHY THAT MATTERS MORE THAN A STRAY CHARACTER. Each validator is followed by an
EXACT comparison that then misses:

    if not DB_NAME_RE.match(db): raise ...                    # "postgres\\n" passes
    if db in RESERVED_DB_NAMES or db == cr.dbname: raise ...  # and MISSES

so the newline defeats the reserved-name and self-target guards, which are the
real control. `_nc_subdomain_availability` has the identical shape for reserved
subdomains.

WHAT WAS *NOT* FOUND, stated so nobody re-derives a scarier version: no live
exploit. The public subdomain path is saved by an upstream `.strip()`, and
`_drop_database` uses `sql.Identifier`, so `"postgres\\n"` is a distinct quoted
identifier rather than `postgres`. The protection is INCIDENTAL — it lives in a
caller's normalisation and in psycopg2's quoting, not in the validator. A
validator whose correctness depends on someone else remembering to strip is not
doing its job, and the next caller may not.

Three anchor bugs shipped in one week before this (#377): a trailing `\\b` that
let `card 4111111111111111_2026` reach a third-party LLM unredacted, the same
bug one level down inside its own guard, and this one. None had a regression
test — which is why each shipped. This file is that missing test, and
`invariants.py` R8 is the guard that fails the next one automatically.
"""

import re

from odoo.tests import TransactionCase, tagged

from ..models import checkout as checkout_model
from ..models import config_sync as config_sync_model
from ..models import fleet_migration as fleet_model
from ..models import provisioning_job as pj_model
from ..models import saas_subprocess as subprocess_model
from ..models import tenant as tenant_model


# (label, compiled regex, a value that is genuinely valid)
ANCHORED_VALIDATORS = (
    ('DB_NAME_RE', subprocess_model.DB_NAME_RE, 'acmetenant'),
    ('SCRATCH_DB_NAME_RE', subprocess_model.SCRATCH_DB_NAME_RE, 'drill_acme'),
    ('_GENERATED_NAME_RE', tenant_model._GENERATED_NAME_RE, 'acmetenant'),
    ('SUBDOMAIN_RE', checkout_model.SUBDOMAIN_RE, 'acmetenant'),
    ('_CLEANUP_NAME_RE', pj_model._CLEANUP_NAME_RE, 'prov_scratch'),
    ('MODULE_NAME_RE', fleet_model.MODULE_NAME_RE, 'sale'),
    ('_XMLID_RE', config_sync_model._XMLID_RE, 'base.group_user'),
)


@tagged('post_install', '-at_install')
class TestAnchoredValidators(TransactionCase):

    def test_every_anchored_validator_rejects_a_trailing_newline(self):
        """The property, asserted directly on the patterns.

        `fullmatch` is what makes this true; with `.match()` every one of these
        returns a match and the assertion fails. Kept as a loop over the real
        compiled objects rather than restating the patterns, so a change to a
        pattern is covered without touching this file.
        """
        for label, rx, good in ANCHORED_VALIDATORS:
            with self.subTest(validator=label):
                self.assertTrue(
                    rx.fullmatch(good),
                    "%s must accept its own valid value %r" % (label, good))
                self.assertIsNone(
                    rx.fullmatch(good + "\n"),
                    "%s accepted a trailing newline — `$` matches before one, "
                    "so .match() would let it through (#377)" % label)

    def test_the_bug_is_real_and_this_test_would_have_caught_it(self):
        """Pins WHY fullmatch is required, not merely that it is used.

        If someone 'simplifies' a validator back to .match(), the loop above
        fails. This asserts the underlying language behaviour that makes it
        fail, so the reason survives even if the loop is rewritten.
        """
        rx = subprocess_model.DB_NAME_RE
        self.assertIsNotNone(rx.match("postgres\n"),
                             "if this stops being true, Python changed and the "
                             "whole rationale here needs revisiting")
        self.assertIsNone(rx.fullmatch("postgres\n"))

    def test_a_newline_defeats_the_reserved_name_guard(self):
        """The consequence, made executable.

        The reserved check is an exact membership test, so a name the regex let
        through with a newline is NOT caught by it. This is the two-layer
        failure, not just a lax pattern — and it is why the fix belongs in the
        validator rather than in another downstream check.
        """
        reserved = subprocess_model.RESERVED_DB_NAMES
        for name in sorted(reserved):
            with self.subTest(reserved=name):
                self.assertIn(name, reserved)
                self.assertNotIn(
                    name + "\n", reserved,
                    "membership is exact — which is correct, and precisely why "
                    "the validator must not admit the newline in the first place")
                self.assertIsNone(subprocess_model.DB_NAME_RE.fullmatch(name + "\n"))

    def test_the_guard_rejects_a_reserved_name_with_a_newline(self):
        """End to end through the real guard, not the regex alone."""
        mixin = self.env['ncollection.saas.subprocess.mixin']
        from odoo.exceptions import ValidationError
        for probe in ('postgres\n', 'admin\n', self.env.cr.dbname + '\n'):
            with self.subTest(db=probe), self.assertRaises(ValidationError):
                mixin._assert_safe_db_name(probe)

    def test_no_anchored_validator_in_this_module_still_uses_match(self):
        """A cheap in-suite mirror of invariants.py R8.

        R8 runs in CI and pre-push; this catches the same mistake in a plain
        `make test` run, where someone iterating on these models is most likely
        to introduce it.
        """
        import inspect
        for module in (subprocess_model, checkout_model, tenant_model,
                       pj_model, fleet_model, config_sync_model):
            src = inspect.getsource(module)
            anchored = {
                m.group(1) for m in re.finditer(
                    r"(\w+)\s*=\s*re\.compile\(\s*r?['\"](\^.*\$)['\"]", src)
            }
            for name in anchored:
                with self.subTest(module=module.__name__, validator=name):
                    self.assertNotRegex(
                        src, r"\b%s\.match\(" % re.escape(name),
                        "%s.%s is anchored but applied with .match(); `$` also "
                        "matches before a trailing newline (#377)"
                        % (module.__name__, name))
