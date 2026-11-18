"""AI pilot role access: admin, allowlisted approved user, everyone else."""
import unittest
from unittest.mock import patch

import ai_order_access


class AIAccessTests(unittest.TestCase):
    @patch("ai_order_access.storage.is_approved")
    @patch("ai_order_access.storage.is_admin")
    @patch("ai_order_access.config.AI_ORDER_PILOT_IDS", {260287389})
    @patch("ai_order_access.config.AI_ORDER_ENABLED", True)
    def test_admin_is_allowed_even_if_not_pilot(self, admin, approved):
        admin.side_effect = lambda uid: uid == 999
        approved.side_effect = lambda uid: uid in (999, 260287389, 111)
        self.assertTrue(ai_order_access.allowed(999))
        self.assertTrue(ai_order_access.allowed(260287389))
        self.assertFalse(ai_order_access.allowed(111))
        self.assertFalse(ai_order_access.allowed(None))

    @patch("ai_order_access.storage.is_approved")
    @patch("ai_order_access.storage.is_admin")
    @patch("ai_order_access.config.AI_ORDER_PILOT_IDS", {260287389})
    @patch("ai_order_access.config.AI_ORDER_ENABLED", False)
    def test_disabled_for_everyone_including_admin(self, admin, approved):
        admin.return_value = True
        approved.return_value = True
        self.assertFalse(ai_order_access.allowed(999))
        self.assertFalse(ai_order_access.allowed(260287389))

    @patch("ai_order_access.storage.is_approved")
    @patch("ai_order_access.storage.is_admin")
    @patch("ai_order_access.config.AI_ORDER_PILOT_IDS", {260287389})
    @patch("ai_order_access.config.AI_ORDER_ENABLED", True)
    def test_unapproved_allowlisted_user_cannot_use_it(self, admin, approved):
        admin.return_value = False
        approved.return_value = False
        self.assertFalse(ai_order_access.allowed(260287389))


if __name__ == "__main__":
    unittest.main()
