# -*- coding: utf-8 -*-
"""Unit tests for ncollection.error.log and telemetry sanitization (P8-T10 / Issue #444)."""
import re
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestErrorLog(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ErrorLog = cls.env['ncollection.error.log']

    def test_incident_id_format(self):
        """Incident IDs must follow ERR-XXXXXX-XXXX uppercase format."""
        incident_id = self.ErrorLog._nc_generate_error_id()
        self.assertTrue(incident_id.startswith('ERR-'))
        pattern = r'^ERR-[0-9A-F]{6}-[0-9A-F]{4}$'
        self.assertTrue(bool(re.match(pattern, incident_id)), f"Invalid ID format: {incident_id}")

    def test_traceback_sanitization(self):
        """Sanitizer must redact passwords, bearer tokens, DSNs, and card numbers."""
        dirty_text = (
            "User login failed: password='placeholderSecret123!' with token=example_token_998124\n"
            "Auth Header: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz\n"
            "Database: postgres://nc_admin:topSecretP@ss@127.0.0.1:5432/ncdb\n"
            "Customer PAN: 4111 2222 3333 4444"
        )
        clean = self.ErrorLog._nc_sanitize_traceback(dirty_text)

        self.assertNotIn("placeholderSecret123!", clean)
        self.assertNotIn("topSecretP@ss", clean)
        self.assertNotIn("4111 2222 3333 4444", clean)
        self.assertIn("password=***", clean)
        self.assertIn("Bearer [MASKED]", clean)
        self.assertIn("postgres://***:***@", clean)
        self.assertIn("[CARD MASKED]", clean)

    def test_log_exception_recording(self):
        """log_exception must create a structured ncollection.error.log record."""
        try:
            # Raise a test division error
            1 / 0
        except ZeroDivisionError as exc:
            incident_id = self.ErrorLog.log_exception(
                exc,
                route='/api/v1/test_fault',
                http_status=500,
                error_type='ZeroDivisionError',
                method='POST',
            )

        self.assertTrue(incident_id.startswith('ERR-'))
        rec = self.ErrorLog.search([('uuid', '=', incident_id)], limit=1)
        self.assertTrue(rec.exists(), "Error log record was not created")
        self.assertEqual(rec.http_status, 500)
        self.assertEqual(rec.route, '/api/v1/test_fault')
        self.assertEqual(rec.method, 'POST')
        self.assertEqual(rec.error_type, 'ZeroDivisionError')
        self.assertIn('ZeroDivisionError: division by zero', rec.traceback_masked)
