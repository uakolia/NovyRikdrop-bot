"""Offline tests: no Telegram, Sheets or Nova Poshta calls."""
import unittest

from delivery_view import delivery_html, delivery_label, ttn_text


class DeliveryViewTest(unittest.TestCase):
    def test_missing_ttn_not_shipped(self):
        self.assertIn("Ще не відправлено", delivery_label({"ttn": ""}))

    def test_created_ttn_does_not_mean_shipped(self):
        self.assertIn("статус НП ще невідомий", delivery_label({"ttn": "20450000000000"}))
        self.assertIn("Ще не відправлено", delivery_label({
            "ttn": "20450000000000",
            "np_status": "Нова пошта очікує надходження від відправника",
        }))

    def test_in_transit(self):
        self.assertIn("Відправлено", delivery_label({
            "ttn": "20450000000000", "np_status": "Відправлення у дорозі",
        }))

    def test_carrier_receipt_is_not_customer_receipt(self):
        self.assertIn("Відправлено", delivery_label({
            "ttn": "20450000000000", "np_status": "Відправлення отримано для відправлення",
        }))

    def test_received(self):
        self.assertIn("✅ Отримано", delivery_label({
            "ttn": "20450000000000", "np_status": "Відправлення отримано",
        }))

    def test_returned_must_not_be_received(self):
        self.assertIn("повернення", delivery_label({
            "ttn": "20450000000000",
            "np_status": "Одержано і створено ЄН зворотньої доставки",
        }))

    def test_unknown_not_assumed_shipped(self):
        self.assertEqual("📦 Статус Нової Пошти", delivery_label({
            "ttn": "20450000000000", "np_status": "Змінено адресу",
        }))

    def test_deleted_or_missing(self):
        self.assertIn("видалено", delivery_label({
            "ttn": "20450000000000", "np_status": "Видалено",
        }))
        self.assertIn("не знайдено", delivery_label({
            "ttn": "20450000000000", "np_status": "Номер не знайдено",
        }))

    def test_safe_html(self):
        result = delivery_html({
            "ttn": "20450000000000", "np_status": "Відправлення у дорозі <b>hello</b>",
        })
        self.assertIn("&lt;b&gt;hello&lt;/b&gt;", result)
        self.assertNotIn("<b>hello</b>", result)

    def test_numeric_ttn(self):
        self.assertEqual("20450000000000", ttn_text(20450000000000.0))
        self.assertEqual("20450000000000", ttn_text("20450000000000.0"))


if __name__ == "__main__":
    unittest.main()
