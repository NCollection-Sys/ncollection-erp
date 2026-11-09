"""The mock's grounded answer: headline figures stated from the data sections of the prompt (and nothing invented)."""
import json
import unittest

from providers import MockProvider, grounded_summary


def _prompt(sections, language="English"):
    return ("You are a business analyst.\nWrite the whole answer in %s (identity tokens stay exactly as written).\n"
            "--- WORKSPACE DATA ---\n%s\n--- END DATA ---\n\nQUESTION: x\n" % (language, json.dumps(sections)))


class GroundedMockTest(unittest.TestCase):
    DATA = {
        "workspace": {"company": "Acme", "currency": "AED"},
        "receivables": [{"partner_id": "PARTNER_1", "amount_residual:sum": 1000.5},
                        {"partner_id": "PARTNER_2", "amount_residual:sum": 500.0}],
        "stock_on_hand": [{"product_id": "PRODUCT_1", "quantity:sum": 42.0}],
    }

    def test_english_answer_states_the_totals_and_says_it_is_a_demo(self):
        text = grounded_summary(_prompt(self.DATA))
        self.assertIn("Demo answer from your workspace data", text)
        self.assertIn("Open customer invoices: 1,500.50 AED (top PARTNER_1)", text)
        self.assertIn("Units on hand (top products): 42 (top PRODUCT_1)", text)

    def test_arabic_answer_uses_arabic_labels(self):
        text = grounded_summary(_prompt(self.DATA, "Arabic"))
        self.assertIn("إجابة تجريبية", text)
        self.assertIn("فواتير العملاء المفتوحة: 1,500.50 AED", text)

    def test_no_data_means_no_invented_figures(self):
        self.assertEqual(grounded_summary("QUESTION: hello"), "")
        self.assertEqual(grounded_summary(_prompt({"workspace": {"currency": "AED"}})), "")
        self.assertEqual(grounded_summary("--- WORKSPACE DATA ---\nnot json\n--- END DATA ---"), "")

    def test_the_streamed_mock_text_still_starts_with_the_marker_and_carries_the_summary(self):
        pieces = [p for p in MockProvider().stream(_prompt(self.DATA), 256) if isinstance(p, str)]
        text = "".join(pieces)
        self.assertTrue(text.startswith("[mock completion]"))
        self.assertIn("1,500.50 AED", text)
        self.assertGreater(len(pieces), 5)


if __name__ == "__main__":
    unittest.main()
