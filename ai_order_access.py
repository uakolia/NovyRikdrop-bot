"""Single authorization rule for the AI order pilot.

Railway ADMIN_IDS and allowlisted approved dropshippers may use the pilot
only while AI_ORDER_ENABLED is true. No broader user access is granted.
"""

import config
import storage


def allowed(user_id: int | None) -> bool:
    if user_id is None or not config.AI_ORDER_ENABLED:
        return False
    return bool(
        storage.is_approved(user_id)
        and (storage.is_admin(user_id) or user_id in config.AI_ORDER_PILOT_IDS)
    )
