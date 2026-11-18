"""Single authorization rule for the AI order pilot.

When AI_ORDER_ALL_APPROVED=1, every approved dropshipper can use the AI
ordering interface. Otherwise only ADMIN_IDS and allowlisted approved users
have access. AI_ORDER_ENABLED=0 always disables it for everybody.
"""

import config
import storage


def allowed(user_id: int | None) -> bool:
    if user_id is None or not config.AI_ORDER_ENABLED:
        return False
    return bool(
        storage.is_approved(user_id)
        and (config.AI_ORDER_ALL_APPROVED
             or storage.is_admin(user_id)
             or user_id in config.AI_ORDER_PILOT_IDS)
    )
