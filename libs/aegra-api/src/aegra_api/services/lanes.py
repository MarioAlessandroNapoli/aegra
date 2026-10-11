"""Job queue lanes: ``chat`` for interactive runs, ``bg`` for long background runs.

Each run declares ``metadata.lane``; workers BLPOP their lanes in priority order.
"""

from collections.abc import Mapping
from typing import Any

from aegra_api.settings import settings

LANES = ("chat", "bg")
LANE_KEY = "lane"


class MissingLaneError(ValueError):
    """A run was created without a valid ``metadata.lane``."""


def lane_of(run_metadata: Mapping[str, Any] | None) -> str:
    lane = (run_metadata or {}).get(LANE_KEY)
    if lane not in LANES:
        raise MissingLaneError(
            f"run metadata.lane must be one of {LANES}, got {lane!r}: the caller creating the run "
            "(athena-backend langgraph_client) must declare the lane in the run metadata"
        )
    return lane


def queue_key(lane: str) -> str:
    """The Redis list of a lane. ``chat`` keeps the historical key, so runs
    enqueued before the lanes existed are still consumed."""
    if lane == "chat":
        return settings.worker.WORKER_QUEUE_KEY
    if lane == "bg":
        return f"{settings.worker.WORKER_QUEUE_KEY}:bg"
    raise MissingLaneError(f"unknown lane {lane!r}, expected one of {LANES}")


def demand_key() -> str:
    """bg runs queued or in flight: the one list the bg worker pool autoscaler reads.

    Two lists (queue + in-flight) would not do: the autoscaler takes the max of its
    triggers, not the sum, so a full instance with runs waiting would not scale up."""
    return f"{queue_key('bg')}:demand"


def consumed_lanes() -> list[str]:
    """Lanes this instance consumes, in priority order."""
    return list(settings.worker.WORKER_LANES)
