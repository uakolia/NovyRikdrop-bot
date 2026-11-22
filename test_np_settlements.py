"""Regression tests: NP settlements are paginated and IDs remain specific."""
import unittest
from unittest.mock import AsyncMock, patch

import novaposhta as np
import handlers_admin
import handlers_ai_order


class SettlementLookupTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_same_named_places_are_accessible_across_pages(self):
        first = [
            {"Present": f"с. Новоселиця, район {i}, Закарпатська обл.",
             "MainDescription": "Новоселиця", "Area": "Закарпатська",
             "Region": f"район {i}", "DeliveryCity": f"delivery-{i}",
             "SettlementRef": f"settlement-{i}"}
            for i in range(8)
        ]
        second = [
            {"Present": "м. Новоселиця, Новоселицький р-н, Чернівецька обл.",
             "MainDescription": "Новоселиця", "Area": "Чернівецька",
             "Region": "Новоселицький", "DeliveryCity": "delivery-9",
             "SettlementRef": "settlement-9"}
        ]
        with patch.object(np, "_call", new_callable=AsyncMock) as call:
            call.side_effect = [
                [{"TotalCount": 9, "Addresses": first}],
                [{"TotalCount": 9, "Addresses": second}],
            ]
            p1 = await np.search_city_page("Новоселиця")
            p2 = await np.search_city_page("Новоселиця", page=2)
        self.assertEqual(len(p1["cities"]), 8)
        self.assertTrue(p1["has_next"])
        self.assertEqual(p2["cities"][0]["ref"], "delivery-9")
        self.assertEqual(p2["cities"][0]["settlement_ref"], "settlement-9")
        self.assertIn("Чернівецька обл.", p2["cities"][0]["label"])
        self.assertFalse(p2["has_next"])
        self.assertEqual(call.call_args_list[1].args[2]["Page"], "2")

    async def test_cannot_create_waybill_for_non_delivery_settlement(self):
        with patch.object(np, "_call", new_callable=AsyncMock) as call:
            call.return_value = [{
                "TotalCount": 2,
                "Addresses": [
                    {"MainDescription": "Тестове село", "SettlementRef": "settlement-only"},
                    {"MainDescription": "Тестове село", "DeliveryCity": "valid-ref",
                     "Area": "Київська", "Region": "Бучанський"}
                ]
            }]
            result = await np.search_city_page("Тестове село")
        self.assertEqual(len(result["cities"]), 1)
        self.assertEqual(result["cities"][0]["ref"], "valid-ref")
        self.assertIn("Київська", result["cities"][0]["label"])
        self.assertFalse(result["has_next"])

    async def test_warehouses_not_limited_to_first_500(self):
        first = [{"Ref": f"ref-{i}", "Number": str(i),
                  "Description": f"Відділення {i}"} for i in range(500)]
        last = [{"Ref": "ref-500", "Number": "500",
                 "Description": "Відділення 500"}]
        with patch.object(np, "_call", new_callable=AsyncMock) as call:
            call.side_effect = [first, last]
            warehouses = await np.all_warehouses("delivery-city-ref")
        self.assertEqual(len(warehouses), 501)
        self.assertEqual(call.call_args_list[1].args[2]["Page"], "2")

    async def test_empty_lookup(self):
        result = await np.search_city_page(" ")
        self.assertEqual(result["cities"], [])
        self.assertFalse(result["has_next"])


class SelectionNavigationTests(unittest.TestCase):
    def test_admin_has_next_page_and_full_disambiguation(self):
        city = {"ref": "delivery-2", "name": "Новоселиця",
                "label": "с. Новоселиця, Тячівський р-н, Закарпатська обл."}
        kb = handlers_admin._npc_kb([city], 1, True)
        labels = [button.text for row in kb.inline_keyboard for button in row]
        callbacks = [button.callback_data for row in kb.inline_keyboard for button in row]
        self.assertTrue(any("Тячівський" in label for label in labels))
        self.assertIn("npc:1:0", callbacks)
        self.assertIn("npcp:2", callbacks)

    def test_ai_has_next_page_and_correct_city_index(self):
        city = {"ref": "delivery-9", "name": "Новоселиця",
                "label": "м. Новоселиця, Чернівецька обл."}
        kb = handlers_ai_order.ai_city_kb([city], 2, False)
        callbacks = [button.callback_data for row in kb.inline_keyboard for button in row]
        self.assertIn("ai:city:2:0", callbacks)
        self.assertIn("ai:cityp:1", callbacks)
        self.assertNotIn("ai:cityp:3", callbacks)


if __name__ == "__main__":
    unittest.main()
