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
exploit. But the first version of this note gave the wrong reason, and the
correction matters for how the pre-fix severity reads.

There are TWO routes, not one:

  * The public subdomain path IS saved by an upstream `.strip()`
    (`_nc_normalize_subdomain`), so a newline never reaches the validator.
  * `tenant.database_name` is NOT. It is a writable Char, exempt from format
    enforcement while `database_status` is `not_provisioned`/`error`, and
    writable by `base.group_system` / `group_platform_admin`. It reaches
    `_assert_safe_db_name` / `_assert_scratch_db_name` through backup path
    checks, a backup-dir rmtree target, a DROP DATABASE and a subprocess `-d`
    arg with NO normalisation anywhere in between.

That second route was closed by nothing upstream. What made it harmless was
DOWNSTREAM: `sql.Identifier` quoting (so `"postgres\\n"` is a distinct
identifier), literal path-segment semantics (a directory named `postgres\\n`
is not `postgres`), and argv-list subprocess calls (no shell).

So the fix CLOSES a real, privilege-gated hole on that route rather than merely
hardening an already-safe one. In both cases the protection was INCIDENTAL —
someone else's normalisation, or psycopg2's quoting — never the validator. A
validator whose correctness depends on that is not doing its job, and the next
caller may not inherit the luck.

COVERAGE, STATED HONESTLY. This file exercises the seven validators owned by
ncollection_saas. Six more were swept and are NOT covered here — the two hex
colours (branding, reseller), the two signup emails (core, saas controller),
and the email/URL shape checks in ncollection_ai — because importing them would
couple this suite to four other addons being installed. Those six rely on
invariants R8, which is a static guard that CI and pre-push run, and which the
review of this ticket found had a live directory-scope bug of its own. That bug
is fixed and R8 is now proved against five shapes including the cross-module
and prose cases; the reliance is deliberate, not an oversight.

Three anchor bugs shipped in one week before this (#377): a trailing `\\b` that
let `card 4111111111111111_2026` reach a third-party LLM unredacted, the same
bug one level down inside its own guard, and this one. None had a regression
test — which is why each shipped. This file is that missing test, and
`invariants.py` R8 is the guard that fails the next one automatically.
"""

from odoo.exceptions import ValidationError
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
        """The consequence, made executable — THROUGH the guard, not the regex.

        An earlier version of this test asserted only
        `DB_NAME_RE.fullmatch(name + "\\n") is None`, which is a property of the
        PATTERN: it would have passed unchanged with every production call site
        reverted to `.match()`. Review counted three such tests in this file
        where I had claimed two. It now drives `_assert_safe_db_name`, so a
        call-site regression fails it.

        The membership assertion stays, because it names WHY the fix belongs in
        the validator: the reserved check is exact, so anything the regex lets
        through with a newline is not caught downstream.
        """
        mixin = self.env['ncollection.saas.subprocess.mixin']
        reserved = subprocess_model.RESERVED_DB_NAMES
        for name in sorted(reserved):
            with self.subTest(reserved=name):
                self.assertNotIn(
                    name + "\n", reserved,
                    "membership is exact — which is correct, and precisely why "
                    "the validator must not admit the newline in the first place")
                with self.assertRaises(ValidationError):
                    mixin._assert_safe_db_name(name + "\n")

    def test_the_guard_rejects_a_reserved_name_with_a_newline(self):
        """End to end through the real guard, not the regex alone."""
        mixin = self.env['ncollection.saas.subprocess.mixin']
        for probe in ('postgres\n', 'admin\n', self.env.cr.dbname + '\n'):
            with self.subTest(db=probe), self.assertRaises(ValidationError):
                mixin._assert_safe_db_name(probe)

    def test_the_cross_module_call_site_rejects_a_newline(self):
        """`provisioning_job._validate_db_name` — the site R8 could not see.

        `DB_NAME_RE` is compiled in saas_subprocess and IMPORTED here, so the
        first version of invariants R8 (which collected anchored names per
        FILE) reported clean with this call site reverted to `.match()` —
        the very shape its own rationale quotes as canonical. R8 is now
        repo-wide and catches it statically; this covers it dynamically, so
        the one call site with no regression protection has two.
        """
        job = self.env['ncollection.provisioning.job']
        for probe in ('clienta\n', 'postgres\n'):
            with self.subTest(db=probe), self.assertRaises(ValidationError):
                job._validate_db_name(probe)
