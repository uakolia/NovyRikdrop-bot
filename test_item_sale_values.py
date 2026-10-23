"""Offline tests for item prices, persisted values and single-item volume fallback."""
import ast
import asyncio
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
import order_pricing as pricing
import shipment_boxes as boxes

class State:
    def __init__(self, payment="часткова"):
        self.data = {"cart": [{"article": "T", "qty": 2}, {"article": "W", "qty": 1}], "payment": payment}
    async def get_data(self):
        return dict(self.data)
    async def update_data(self, **kw):
        self.data.update(kw)
    async def set_state(self, value):
        self.current = value

class PriceTests(unittest.TestCase):
    def test_package_values(self):
        items = [({"box": "50*50*160 35*35*120", "weight_kg": "40,38"}, 2), ({"box": "60*60*15", "weight_kg": 2.35}, 1)]
        shipment = pricing.priced_shipment(items, [30000, 1000])
        values = [int(p["cost"]) for p in shipment["options_seat"]]
        self.assertEqual(sum(values[:2]), 15000)
        self.assertEqual(sum(values[2:4]), 15000)
        self.assertEqual(values[4], 1000)
        self.assertEqual(sum(values), shipment["cost"])
        self.assertAlmostEqual(shipment["weight"], 83.11)

    def test_persistence(self):
        rows = [{"qty": 2, "comment": pricing.keep_comment(pricing.sale_comment(15000, 2), "Помилка ТТН")}, {"qty": 1, "comment": pricing.sale_comment(1000, 1)}]
        self.assertEqual(pricing.line_values(rows), [30000, 1000])
        rows[0]["qty"] = 3
        with self.assertRaises(boxes.PackagingError):
            pricing.line_values(rows)
        with self.assertRaises(boxes.PackagingError):
            pricing.line_values([{"qty": 1, "sale_price": 31000}, {"qty": 1, "sale_price": 31000}])
        self.assertEqual(pricing.line_values([{"qty": 2, "sale_price": 30000}]), [30000])

    def test_volume_is_not_weight(self):
        item = {"box": "", "volume_m3": .4, "weight_kg": 26}
        got = pricing.priced_shipment([(item, 1)], [10000])
        self.assertEqual((got["volume"], got["weight"], got["seats"]), (.4, 26, 1))
        item["weight_kg"] = None
        self.assertEqual(boxes.build_shipment([(item, 1)])["weight"], 10)
        item["box"] = "40*40*130"
        self.assertAlmostEqual(boxes.build_shipment([(item, 1)])["volume"], .208)
        item["box"] = "40*40"
        with self.assertRaises(boxes.PackagingError):
            boxes.build_shipment([(item, 1)])

    def test_column_t(self):
        import catalog_parser
        heads = [""]*24
        for i,v in {8:"Артикул",15:"Вага",18:"Розмір коробки",19:"Об’ємна вага",23:"Ціна дроп 2, грн"}.items():
            heads[i] = v
        m = catalog_parser._header_map(heads)
        self.assertEqual((m["weight"], m["box"], m["volume"]), (15,18,19))

    def test_checkout(self):
        async def run(payment):
            state, events = State(payment), []
            async def event(*args, **kw):
                events.append(args)
            flow = types.SimpleNamespace(cart_of=lambda d: d.get("cart", []), catalog=types.SimpleNamespace(by_article=lambda a: {"article":a}, product_name=lambda uid,item: item["article"]), _uid=lambda t:1, _send=event, _back_kb=lambda s:None, Order=types.SimpleNamespace(sale_price="sale_price"), show_category=event, show_prepaid=event, show_lname=event, show_payment=event)
            msg = types.SimpleNamespace(text="1000", answer=event)
            with patch.dict(sys.modules, {"handlers_order": flow}):
                await pricing.input_price(msg,state)
                self.assertEqual(state.data["sale_price_idx"],1)
                msg.text="200"
                await pricing.input_price(msg,state)
                self.assertEqual(state.data["sale_price"],2200)
                if payment=="передплата":
                    self.assertEqual((state.data["prepaid"],state.data["cod_amount"]),(2200,0))
                elif payment=="післяплата":
                    self.assertEqual(state.data["cod_amount"],2200)
                await pricing.back_price(msg,state)
                self.assertEqual(state.data["sale_price_idx"],0)
                before=state.data["cart"][0]["unit_sale_price"]
                msg.text="100.50"
                await pricing.input_price(msg,state)
                self.assertEqual(state.data["cart"][0]["unit_sale_price"],before)
        for payment in ["часткова","передплата","післяплата"]:
            asyncio.run(run(payment))

if __name__ == "__main__":
    unittest.main()
