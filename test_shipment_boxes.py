"""Offline tests only: never create a real Nova Poshta shipment."""
import ast
import asyncio
import datetime
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
import shipment_boxes as boxes

class PackagingTests(unittest.TestCase):
    def test_mixed(self):
        tree = {"article": "T", "box": "50*50*160 35*35*120", "weight_kg": "40,38"}
        wreath = {"article": "W", "box": "50×50×15", "weight_kg": 2}
        got = boxes.build_shipment([(tree, 1), (wreath, 2)])
        self.assertEqual(got["seats"], 4)
        self.assertAlmostEqual(got["weight"], 44.38)
        self.assertAlmostEqual(got["volume"], 0.622)
        self.assertEqual([p["volumetricLength"] for p in got["options_seat"]], ["50", "35", "50", "50"])
        self.assertEqual(boxes.place_count([(tree, 2)]), 4)

    def test_weight_fallback(self):
        for weight in [None, "", "bad", 0, -1, "NaN", "Infinity"]:
            got = boxes.build_shipment([({"box": "40*40*120", "weight_kg": weight}, 2)])
            self.assertEqual(got["weight"], 20)
        got = boxes.build_shipment([({"box": "40*40*120", "weight_kg": 77, "weight_estimated": True}, 1)])
        self.assertEqual(got["weight"], 10)

    def test_invalid(self):
        for value in ["", "50*50", "0*50*160", "-50*50*160", "50*50*160 garbage"]:
            with self.assertRaises(boxes.PackagingError):
                boxes.parse_boxes(value)
        for qty in [0, -1, 1.5]:
            with self.assertRaises(boxes.PackagingError):
                boxes.build_shipment([({"box": "40*40*120"}, qty)])

    def test_columns(self):
        import catalog_parser
        headers = [""] * 24
        for i, v in {8: "Артикул", 9: "Висота, м", 15: "Вага", 16: "Кількість частин в ялинці", 18: "Розмір коробки", 19: "Об'ємна вага", 23: "Ціна дроп 2, грн"}.items():
            headers[i] = v
        m = catalog_parser._header_map(headers)
        self.assertEqual((m["article"], m["weight"], m["box"], m["price_drop2"]), (8, 15, 18, 23))

    def test_ttn_payload_offline(self):
        config = types.SimpleNamespace(NP_PAYER_TYPE="Recipient", NP_PAYMENT_CONTROL=True, now=lambda: datetime.datetime(2026, 10, 22))
        sender = {k: "TEST" for k in ["city_ref", "sender_ref", "warehouse_ref", "contact_ref", "phone"]}
        stubs = {"aiohttp": types.ModuleType("aiohttp"), "config": config,
                 "http_client": types.ModuleType("http_client"), "perf": types.ModuleType("perf"),
                 "np_store": types.SimpleNamespace(is_ready=lambda: True, all_values=lambda: sender)}
        with patch.dict(sys.modules, stubs):
            spec = importlib.util.spec_from_file_location("np_under_test", "novaposhta.py")
            np = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(np)
            captured = {}
            async def recipient(*args):
                return "TEST", "TEST"
            async def call(model, method, props):
                self.assertEqual((model, method), ("InternetDocument", "save"))
                captured.update(props)
                return [{"IntDocNumber": "OFFLINE-TEST"}]
            np.create_recipient, np._call = recipient, call
            shipment = boxes.build_shipment([({"box": "50*50*160 35*35*120", "weight_kg": 30}, 1)])
            args = dict(recipient_city_ref="TEST", recipient_warehouse_ref="TEST", fio="TEST", phone="TEST", description="Tree T", cost=100, weight=shipment["weight"], volume=shipment["volume"], seats=shipment["seats"], options_seat=shipment["options_seat"], cod_amount=50)
            result = asyncio.run(np.create_ttn(**args))
            self.assertEqual(result["ttn"], "OFFLINE-TEST")
            self.assertEqual(captured["SeatsAmount"], "2")
            self.assertEqual(captured["OptionsSeat"], shipment["options_seat"])
            self.assertAlmostEqual(float(captured["Weight"]), 30)
            self.assertAlmostEqual(float(captured["VolumeGeneral"]), .547)
            self.assertEqual(captured["AfterpaymentOnGoodsCost"], "50")
            args["options_seat"] = None
            with self.assertRaises(np.NPError):
                asyncio.run(np.create_ttn(**args))

if __name__ == "__main__":
    unittest.main()
