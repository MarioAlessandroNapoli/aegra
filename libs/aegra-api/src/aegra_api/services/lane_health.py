"""Health of the job lanes: oldest unclaimed pending run per lane, and the bg demand list.

The bg lane may be consumed only by a separate worker pool scaled on ``bg:demand``.
If that pool or its autoscaler stops, bg runs wait forever with no error anywhere:
the reaper reconciles the demand list with the runs table and raises the stall alarm.
"""

from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from aegra_api.core.orm import Run as RunORM
from aegra_api.core.redis_manager import redis_manager
from aegra_api.services.lanes import LANE_KEY, LANES, demand_key
from aegra_api.settings import settings

logger = structlog.getLogger(__name__)

ACTIVE = ("pending", "running")


def _lane_column():  # noqa: ANN202 — SQLAlchemy expression
    return func.jsonb_extract_path_text(RunORM.execution_params, "run_metadata", LANE_KEY)


async def pending_by_lane(session: AsyncSession) -> dict[str, dict[str, float | int | None]]:
    """Per lane: unclaimed pending runs and how long the oldest has been pending
    (updated_at: set when a requeue or the reaper puts a run back to pending)."""
    # GROUP BY sull'alias: ripetere l'espressione JSON la rilega con parametri nuovi
    # ($4, $5) e Postgres non la riconosce come la stessa colonna del SELECT.
    rows = await session.execute(
        select(_lane_column().label("lane"), func.count(), func.min(RunORM.updated_at))
        .where(RunORM.status == "pending", RunORM.claimed_by.is_(None))
        .group_by(text("lane"))
    )
    now = datetime.now(UTC)
    found = {lane: (n, oldest) for lane, n, oldest in rows.all()}
    return {
        lane: {
            "pending": found[lane][0] if lane in found else 0,
            "oldest_pending_s": round((now - found[lane][1]).total_seconds()) if lane in found else None,
        }
        for lane in LANES
    }


def stalled(lanes: dict[str, dict[str, float | int | None]]) -> list[str]:
    limit = settings.worker.LANE_STALL_ALARM_SECONDS
    return [lane for lane, v in lanes.items() if v["oldest_pending_s"] is not None and v["oldest_pending_s"] > limit]


async def reconcile_demand(session: AsyncSession) -> tuple[int, int]:
    """Make ``bg:demand`` hold exactly the active bg runs.

    The list is read before the table: a run is committed before it is enqueued, so
    an id in the list is always visible to the query that follows. A run added in
    between may end up twice; finishing it removes every copy (LREM count 0).
    Returns (added, removed)."""
    client = redis_manager.get_client()
    key = demand_key()
    listed = set(await client.lrange(key, 0, -1))  # type: ignore[misc]
    rows = await session.execute(select(RunORM.run_id).where(RunORM.status.in_(ACTIVE), _lane_column() == "bg"))
    active = {str(r[0]) for r in rows.all()}
    missing, gone = active - listed, listed - active
    if missing:
        await client.rpush(key, *missing)  # type: ignore[misc]
    for run_id in gone:
        await client.lrem(key, 0, run_id)  # type: ignore[misc]
    return len(missing), len(gone)
