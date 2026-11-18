"""Offline checks: sample orders must never become real shipping orders."""
import unittest

from demo_order_rows import DEMO_ORDER_COUNT, DEMO_TELEGRAM_ID, for_user
from order_pagination import group_orders


class DummyOrderTests(unittest.TestCase):
    def test_only_authorized_user(self):
        self.assertEqual([], for_user(111111111))
        self.assertEqual([], for_user(0))
        self.assertTrue(for_user(DEMO_TELEGRAM_ID))

    def test_exactly_35_orders(self):
        groups = group_orders(for_user(DEMO_TELEGRAM_ID))
        self.assertEqual(DEMO_ORDER_COUNT, len(groups))
        self.assertEqual("DEMO-035", groups[0][0]["order_no"])
        self.assertEqual("DEMO-001", groups[-1][0]["order_no"])

    def test_each_item_is_demo(self):
        for row in for_user(DEMO_TELEGRAM_ID):
            self.assertEqual(str(DEMO_TELEGRAM_ID), row["dropshipper_id"])
            self.assertEqual("TEST ONLY", row["source"])
            self.assertIn("ТЕСТ", row["product"])
            self.assertTrue(row["article"].startswith("TEST-NO-STOCK-"))
            self.assertNotEqual("1", row.get("price_drop"))
            self.assertIn("НЕ ВІДПРАВЛЯТИ", row["recipient_fio"])
            self.assertFalse(str(row.get("ttn") or "").isdigit())

    def test_mixed_statuses_and_multi_item_orders(self):
        groups = group_orders(for_user(DEMO_TELEGRAM_ID))
        self.assertTrue(any(len(group) > 1 for group in groups))
        self.assertTrue(any(not group[0]["ttn"] for group in groups))
        self.assertTrue(any(group[0]["np_status"] == "Відправлення у дорозі"
                            for group in groups))
        self.assertTrue(any(group[0]["np_status"] == "Відправлення отримано"
                            for group in groups))


if __name__ == "__main__":
    unittest.main()
