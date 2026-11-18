"""Pure validation tests for AI ordering; do not call OpenAI or Nova Poshta."""
import unittest

from ai_order_core import cm, normalize_phone, plan, resolve_item

ENTRIES = [
    {"article": "Cr3-230", "size": "2.3 м",
     "names": ["Українська", "Українська Люкс 2.3м"]},
    {"article": "Cr3-250", "size": "2.5 м",
     "names": ["Українська", "Українська Люкс 2.5м"]},
    {"article": "Cr4-230", "size": "2.3 м",
     "names": ["Класична", "Класична 2.3 м"]},
]
DRAFT = {
    "items": [{"query": "Українська Люкс 2.3м", "size": "230",
               "quantity": 2, "unit_sale_price": 8500}],
    "payment": "післяплата", "prepaid": 0,
    "fio": "Петренко Іван", "phone": "0671234567",
    "city": "Київ", "warehouse_number": "№45",
}


class CoreTests(unittest.TestCase):
    def test_heights(self):
        for value in ("230", "2.3м", "2,3 м", "230 см"):
            with self.subTest(value=value):
                self.assertEqual(230, cm(value))

    def test_exact_personal_name(self):
        product, problem = resolve_item(DRAFT["items"][0], ENTRIES)
        self.assertEqual("Cr3-230", product["article"])
        self.assertFalse(problem)

    def test_sku_lookup(self):
        product, problem = resolve_item({"query": "Cr3-250", "size": "2.5 м"}, ENTRIES)
        self.assertEqual("Cr3-250", product["article"])
        self.assertFalse(problem)

    def test_contradictory_sku_size_rejected(self):
        product, problem = resolve_item({"query": "Cr3-250", "size": "2.3 м"}, ENTRIES)
        self.assertIsNone(product)
        self.assertIn("не відповідає", problem)

    def test_ambiguous_model_rejected(self):
        product, problem = resolve_item({"query": "Українська Люкс", "size": ""}, ENTRIES)
        self.assertIsNone(product)
        self.assertIn("кілька", problem)

    def test_phone(self):
        self.assertEqual("+380671234567", normalize_phone("0671234567"))
        self.assertEqual("+380671234567", normalize_phone("+38 (067) 123-45-67"))
        self.assertEqual("", normalize_phone("12345"))

    def test_full_order(self):
        fields, problems = plan(DRAFT, ENTRIES)
        self.assertEqual([], problems)
        self.assertEqual([{"article": "Cr3-230", "qty": 2,
                           "unit_sale_price": 8500}], fields["cart"])
        self.assertEqual(17000, fields["cod_amount"])
        self.assertEqual("45", fields["warehouse_number"])

    def test_missing_price_and_payment(self):
        data = {**DRAFT, "payment": "",
                "items": [{"query": "Українська", "size": "230",
                           "quantity": 1, "unit_sale_price": 0}]}
        _, problems = plan(data, ENTRIES)
        self.assertTrue(any("ціну" in problem for problem in problems))
        self.assertTrue(any("оплати" in problem for problem in problems))

    def test_partial_payment(self):
        fields, problems = plan({**DRAFT, "payment": "часткова", "prepaid": 2000}, ENTRIES)
        self.assertFalse(problems)
        self.assertEqual(15000, fields["cod_amount"])

    def test_wrong_partial_payment(self):
        _, problems = plan({**DRAFT, "payment": "часткова", "prepaid": 17001}, ENTRIES)
        self.assertTrue(any("часткової" in problem for problem in problems))

    def test_unknown_product(self):
        data = {**DRAFT, "items": [{"query": "Неіснуюча ялинка", "size": "2.3м",
                                   "quantity": 1, "unit_sale_price": 8500}]}
        fields, problems = plan(data, ENTRIES)
        self.assertEqual([], fields["cart"])
        self.assertTrue(any("Не знайдено" in problem for problem in problems))

    def test_duplicate_item(self):
        data = {**DRAFT, "items": [DRAFT["items"][0], DRAFT["items"][0]]}
        _, problems = plan(data, ENTRIES)
        self.assertTrue(any("двічі" in problem for problem in problems))


if __name__ == "__main__":
    unittest.main()
