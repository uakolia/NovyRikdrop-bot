"""Offline tests for page size, multi-item orders, and pagination boundaries."""
import unittest

from order_pagination import PAGE_SIZE, group_orders, load_page, order_token


def rows(n: int, items: int = 1):
    result = []
    for number in range(n, 0, -1):
        for i in range(items):
            result.append({
                "order_no": str(number),
                "created_at": f"2026-11-{(number % 28) + 1:02}",
                "article": f"sku-{i}",
            })
    return result


class PaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_page_and_second_page(self):
        all_rows = rows(25)

        async def fetch(limit):
            return all_rows[:limit], None

        first = await load_page(fetch, 0)
        self.assertEqual(10, len(first.groups))
        self.assertEqual("25", first.groups[0][0]["order_no"])
        self.assertEqual("16", first.groups[-1][0]["order_no"])
        self.assertTrue(first.has_next)

        third = await load_page(fetch, 2)
        self.assertEqual(5, len(third.groups))
        self.assertEqual("5", third.groups[0][0]["order_no"])
        self.assertFalse(third.has_next)

    async def test_zero_or_ten_orders(self):
        for n in (0, 10):
            all_rows = rows(n)

            async def fetch(limit):
                return all_rows[:limit], None

            page = await load_page(fetch, 0)
            self.assertEqual(n, len(page.groups))
            self.assertFalse(page.has_next)

    async def test_multiple_rows_per_order(self):
        all_rows = rows(23, items=4)

        async def fetch(limit):
            return all_rows[:limit], None

        page = await load_page(fetch, 1)
        self.assertEqual(10, len(page.groups))
        self.assertEqual("13", page.groups[0][0]["order_no"])
        self.assertTrue(all(len(group) == 4 for group in page.groups))
        self.assertTrue(page.has_next)

    async def test_stable_detail_tokens(self):
        a = [{"order_no": "32", "created_at": "today", "article": "A"}]
        b = [{"order_no": "33", "created_at": "today", "article": "A"}]
        self.assertEqual(order_token(a), order_token(a))
        self.assertNotEqual(order_token(a), order_token(b))

    async def test_fetch_error(self):
        async def fetch(limit):
            return None, "unavailable"

        page = await load_page(fetch, 0)
        self.assertEqual([], page.groups)
        self.assertEqual("unavailable", page.error)

    async def test_grouping_keeps_one_order_together(self):
        grouped = group_orders([
            {"order_no": "A", "article": "1"},
            {"order_no": "A", "article": "2"},
            {"order_no": "B", "article": "3"},
        ])
        self.assertEqual([2, 1], [len(group) for group in grouped])

    async def test_bad_page_rejected(self):
        async def fetch(limit):
            return rows(2)[:limit], None

        with self.assertRaises(ValueError):
            await load_page(fetch, -1)

    async def test_page_size(self):
        self.assertEqual(10, PAGE_SIZE)


if __name__ == "__main__":
    unittest.main()
