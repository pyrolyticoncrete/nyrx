# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import logging
from datetime import UTC, datetime

from .db import save_sc_followed

logger = logging.getLogger(__name__)


def is_sc_followed(uploader_id: str, followed: list[dict]) -> bool:
    return any(a.get("id") == uploader_id for a in followed)


def follow_sc(
    uploader_id: str, permalink: str, name: str, url: str, followed: list[dict]
) -> dict:
    entry = {
        "id": uploader_id,
        "permalink": permalink,
        "name": name,
        "url": url,
        "followed_at": datetime.now(UTC).isoformat(),
    }
    followed.append(entry)
    save_sc_followed(followed)
    logger.debug("follow_sc: uploader_id=%s name=%s", uploader_id, name[:20])
    return entry


def unfollow_sc(uploader_id: str, followed: list[dict]) -> None:
    for i, a in enumerate(followed):
        if a.get("id") == uploader_id:
            followed.pop(i)
            break
    save_sc_followed(followed)
    logger.debug("unfollow_sc: uploader_id=%s", uploader_id)
