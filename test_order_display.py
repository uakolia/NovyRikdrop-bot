"""Prevent duplicate tree heights in My Orders list and detail views."""

import unittest

from order_display import product_with_height


class ProductHeightTests(unittest.TestCase):
    def test_customer_examples(self):
        examples = [
            ("Українська Люкс 2.3м", "230"),
            ("Нью Йорк 2.6м", "2.6 м"),
            ("Кастом Ексклюзив 1.8м", "1.8 м"),
            ("Кастом Ексклюзив 2.4м", "2.4 м"),
            ("Українська Люкс 2.1м", "2.1 м"),
            ("Нью Йорк 2.2м", "2.2 м"),
        ]
        for name, height in examples:
            with self.subTest(name=name, height=height):
                self.assertEqual(name, product_with_height(name, height))

    def test_centimeters_and_decimal_comma(self):
        self.assertEqual("Tree 2,3 м", product_with_height("Tree 2,3 м", "230 см"))
        self.assertEqual("Tree 230", product_with_height("Tree 230", "2.3 м"))
        self.assertEqual("Tree 2.3m", product_with_height("Tree 2.3m", "230"))

    def test_no_embedded_height(self):
        self.assertEqual("Tree — 2.3 м", product_with_height("Tree", "2.3 м"))

    def test_contradictory_height_kept(self):
        self.assertEqual("Tree 2.3м — 250", product_with_height("Tree 2.3м", "250"))

    def test_year_not_height(self):
        self.assertEqual("Tree 2026 — 230", product_with_height("Tree 2026", "230"))

    def test_missing_values(self):
        self.assertEqual("Tree 2.3м", product_with_height("Tree 2.3м", ""))
        self.assertEqual("2.3 м", product_with_height("", "2.3 м"))

    def test_numbers_not_repeated_when_real_height_present(self):
        self.assertEqual("Tree 2026 2.3м",
                         product_with_height("Tree 2026 2.3м", "230"))


if __name__ == "__main__":
    unittest.main()
