import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import handlers_order as order
import np_directory as directory
import novaposhta as np
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from order_storage import OrderStorage


class OrderRecovery(unittest.IsolatedAsyncioTestCase):
    async def test_failed_refresh_restart_then_retry(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "drafts.db"
            storage = OrderStorage(path)
            key = StorageKey(bot_id=1, chat_id=2, user_id=3)
            state = FSMContext(storage, key)
            await state.set_state(order.Order.warehouse)
            old = dict(ref="old", name="Відділення 1", cargo=True, number="1", max_weight=0)
            await state.update_data(city={"ref": "c", "name": "Село"}, warehouses=[old],
                                    cart=[{"article": "example", "qty": 1}], prepaid=1500)
            cb = AsyncMock()
            cb.data = "wh_refresh"
            with patch.object(np, "city_warehouses", side_effect=np.NPError("Too many requests")), patch.object(order, "_send") as send:
                await order.pick_delivery_warehouse(cb, state)
                buttons = send.await_args.kwargs["reply_markup"].inline_keyboard
                actions = [b.callback_data for row in buttons for b in row]
                self.assertIn("whref:old", actions)
                self.assertIn("wh_refresh", actions)
            await storage.close()
            storage = OrderStorage(path)
            state = FSMContext(storage, key)
            self.assertEqual(await state.get_state(), "Order:warehouse")
            fresh = dict(old, ref="new")
            with patch.object(np, "city_warehouses", return_value=[fresh]) as fetch, patch.object(order, "_send"):
                await order.pick_delivery_warehouse(cb, state)
                fetch.assert_awaited_once_with("c", refresh=True)
            data = await state.get_data()
            self.assertEqual(data["warehouses"], [fresh])
            self.assertEqual(data["prepaid"], 1500)
            self.assertEqual(data["cart"][0]["qty"], 1)
            await state.clear()
            self.assertEqual(await state.get_data(), {})
            await storage.close()

    async def test_directory_and_failed_rebuild(self):
        fixtures = {
            "getCities": [dict(Ref="c", Description="Тест", SettlementRef="s")],
            "getSettlements": [dict(Ref="s", Description="Тест", AreaDescription="Область")],
            "getWarehouses": [dict(Ref="w", CityRef="c", Number="1", Description="Відділення")],
        }

        async def pages(method):
            yield fixtures[method]

        with tempfile.TemporaryDirectory() as root, patch.object(directory, "path", return_value=Path(root)/"np.db"):
            with patch.object(directory, "pages", pages):
                await directory.sync()
            with patch.object(np, "_call", side_effect=AssertionError("unexpected live lookup")):
                self.assertEqual((await np.search_city_page("тест"))["total"], 1)
                self.assertEqual((await np.city_warehouses("c"))[0]["ref"], "w")
            fixtures["getWarehouses"] = []
            with patch.object(directory, "pages", pages), self.assertRaises(ValueError):
                await directory.sync()
            self.assertEqual(directory.warehouses("c")[0]["ref"], "w")
