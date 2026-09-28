from __future__ import annotations

from collections.abc import Collection


def is_admin(user_id: int, admin_ids: Collection[int]) -> bool:
    return user_id in admin_ids
