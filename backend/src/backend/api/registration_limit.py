"""Global rate limit on `POST /agents` (registration).

Registration is public and sends one API-key email per success, so without a cap
anyone could make us mail any number of addresses. The cap is global, not per IP
(see ``config.REGISTER_LIMIT_PER_HOUR`` for why), and counts what we actually
sent: rows in ``agents`` created inside each rolling window. A registration whose
email fails rolls back, sends nothing, and so isn't counted.

``enforce_registration_limit`` takes a transaction-scoped Postgres advisory lock
before counting. The lock is held until the request's session commits or rolls
back, which is after the new row and its email, so concurrent registrations
queue behind each other and can never both slip under the cap.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import config
from ..db.models import Agent

log = logging.getLogger(__name__)

# Arbitrary but fixed key: the ASCII bytes of "REGIST" (the digest uses "DIGEST").
_REGISTER_LOCK_KEY = 0x524547495354  # b"REGIST"

_HOUR = timedelta(hours=1)
_DAY = timedelta(days=1)


def _seconds_until_room(created: list[str], cap: int, window: timedelta, now: datetime) -> int:
    """Seconds until fewer than ``cap`` of ``created`` (ascending ISO times) are in the window.

    0 when there is room now. The registration that must age out is the
    ``len - cap``-th oldest (0-based); once it leaves the window, ``cap - 1`` remain.
    """
    if len(created) < cap:
        return 0
    must_expire = datetime.fromisoformat(created[len(created) - cap])
    return max(1, math.ceil((must_expire + window - now).total_seconds()))


async def enforce_registration_limit(session: AsyncSession) -> None:
    """Raise 429 (with Retry-After) if the hourly or daily registration cap is full."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _REGISTER_LOCK_KEY})

    now = datetime.now(UTC)
    # created_at is an ISO-8601 UTC string from datetime.isoformat(), so string
    # order is time order (the digest's new_since count relies on the same).
    day_cutoff = (now - _DAY).isoformat()
    hour_cutoff = (now - _HOUR).isoformat()
    rows = await session.execute(
        select(Agent.created_at).where(Agent.created_at > day_cutoff).order_by(Agent.created_at)
    )
    in_day = list(rows.scalars())
    in_hour = [c for c in in_day if c > hour_cutoff]

    retry_after = max(
        _seconds_until_room(in_hour, config.REGISTER_LIMIT_PER_HOUR, _HOUR, now),
        _seconds_until_room(in_day, config.REGISTER_LIMIT_PER_DAY, _DAY, now),
    )
    if retry_after == 0:
        return

    # No name or email here: this line goes to the platform's log.
    log.warning(
        "registration limit reached: %d in the last hour (cap %d), %d in the last day "
        "(cap %d); answering 429, retry in %d s",
        len(in_hour),
        config.REGISTER_LIMIT_PER_HOUR,
        len(in_day),
        config.REGISTER_LIMIT_PER_DAY,
        retry_after,
    )
    raise HTTPException(
        status_code=429,
        detail="too many registrations right now; please retry later",
        headers={"Retry-After": str(retry_after)},
    )
