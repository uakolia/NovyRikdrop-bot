"""Check professional AI prompts and real TTN handoff without external writes."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import handlers_order
import handlers_ai_order


class FormalCopyTests(unittest.TestCase):
    def test_correct_example_and_formal_language(self):
        intro = handlers_ai_order.AI_INTRO
        self.assertIn("Напишіть товар, розмір", intro)
        self.assertIn("Українська Преміум", intro)
        self.assertIn("Вашого підтвердження", intro)
        for informal in ("Напиши ", "Введи ", "твого підтвердження"):
            self.assertNotIn(informal, intro)

    def test_both_entrypoints_reuse_same_intro(self):
        import inspect
        self.assertIn("AI_INTRO", inspect.getsource(handlers_ai_order.start_ai))
        self.assertIn("AI_INTRO", inspect.getsource(handlers_ai_order.command_ai))


class LiveTTNFlowTests(unittest.IsolatedAsyncioTestCase):
    def make_context(self):
        cb = SimpleNamespace(
            from_user=SimpleNamespace(id=8098610564),
            answer=AsyncMock(),
            message=SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock()),
        )
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={
                "ai_draft": {"items": []},
                "cart": [{"article": "TEST", "qty": 1, "unit_sale_price": 1000}],
                "cod_amount": 0,
            }),
            clear=AsyncMock(),
        )
        return cb, state

    async def test_live_confirm_routes_into_normal_ttn_creation(self):
        cb, state = self.make_context()
        with (
            patch.object(handlers_order.config, "AI_ORDER_DRY_RUN", False),
            patch.object(handlers_order, "cart_shortage", new_callable=AsyncMock, return_value=[]),
            patch.object(handlers_order, "cart_total", return_value=1000),
            patch.object(handlers_order.payment, "due_amount", return_value=0),
            patch.object(handlers_order, "_finalize", new_callable=AsyncMock) as finalize,
        ):
            await handlers_order.confirm_order(cb, state)
            finalize.assert_awaited_once_with(cb, state, proof_file_id=None, create_ttn=True)
            state.clear.assert_not_awaited()

    async def test_dry_run_never_creates_real_ttn(self):
        cb, state = self.make_context()
        with (
            patch.object(handlers_order.config, "AI_ORDER_DRY_RUN", True),
            patch.object(handlers_order, "_finalize", new_callable=AsyncMock) as finalize,
        ):
            await handlers_order.confirm_order(cb, state)
            finalize.assert_not_awaited()
            state.clear.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
