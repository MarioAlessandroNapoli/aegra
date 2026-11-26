"""Serving gate: a Cloud Run revision without traffic stops taking runs from the queue (AE-1689).

An instance outlives its revision's traffic (minScale, or open SSE streams after a delete) and
its workers kept running new runs with the old code; the traffic split decides who dequeues.
"""

import asyncio
import os
import time

import httpx
import structlog

from aegra_api.settings import settings

logger = structlog.getLogger(__name__)

_METADATA_TOKEN_URL = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
_METADATA_HEADERS = {"Metadata-Flavor": "Google"}
# Renew the access token this long before it expires.
_TOKEN_MARGIN_SECONDS = 60
_HTTP_TIMEOUT_SECONDS = 5.0


class ServingGate:
    """Tracks whether this revision serves traffic; the worker loops read ``consuming``."""

    def __init__(self) -> None:
        self.consuming = True
        self._task: asyncio.Task[None] | None = None
        self._client: httpx.AsyncClient | None = None
        self._token = ""
        self._token_expires_at = 0.0
        self._revision = ""
        self._url = ""

    @property
    def revision(self) -> str:
        return self._revision

    @property
    def enabled(self) -> bool:
        return bool(settings.worker.SERVING_GATE_SERVICE)

    async def start(self) -> None:
        """First check before the workers start, so a revision born without traffic never dequeues."""
        if not self.enabled:
            return
        self._revision = os.environ.get("K_REVISION", "")
        if not self._revision:
            raise RuntimeError(
                "SERVING_GATE_SERVICE is set but K_REVISION is not: the serving gate only runs on a "
                "Cloud Run service; unset SERVING_GATE_SERVICE where K_REVISION does not exist"
            )
        w = settings.worker
        # v1 (Knative) API, the shape `gcloud run services describe` prints and deploy-aegra/release-lib.sh
        # in AE-Agent reads: status.traffic[] = {revisionName, percent, latestRevision, tag}.
        self._url = (
            f"https://{w.SERVING_GATE_REGION}-run.googleapis.com/apis/serving.knative.dev/v1/"
            f"namespaces/{w.SERVING_GATE_PROJECT}/services/{w.SERVING_GATE_SERVICE}"
        )
        self._client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS)
        await self._check()
        logger.info(
            "Serving gate started",
            revision=self._revision,
            service=w.SERVING_GATE_SERVICE,
            consuming=self.consuming,
            interval_s=w.SERVING_GATE_INTERVAL_SECONDS,
        )
        self._task = asyncio.create_task(self._loop())
        self._task.add_done_callback(_report_crash)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(settings.worker.SERVING_GATE_INTERVAL_SECONDS)
            await self._check()

    async def _check(self) -> None:
        try:
            serving = await self._serving_revisions()
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            # #1082 (AE-1689): an unanswered or unreadable check (network, 403, 429, 5xx, malformed
            # body) keeps the worker consuming with an ERROR each time: no chat lane left without consumers.
            logger.error(
                "Serving check failed, consuming anyway",
                revision=self._revision,
                error=f"{type(exc).__name__}: {exc}",
            )
            self._set(True, "serving check failed, #1082: keep consuming", serving=None)
            return
        if self._revision in serving:
            self._set(True, "revision serves traffic", serving=serving)
        else:
            self._set(False, "revision not serving traffic", serving=serving)

    def _set(self, consuming: bool, reason: str, *, serving: list[str] | None) -> None:
        if consuming == self.consuming:
            return
        self.consuming = consuming
        event = "Worker resumed consuming" if consuming else "Worker stopped consuming"
        logger.warning(event, revision=self._revision, reason=reason, serving=serving)

    async def _serving_revisions(self) -> list[str]:
        assert self._client is not None
        token = await self._access_token()
        response = await self._client.get(self._url, headers={"Authorization": f"Bearer {token}"})
        response.raise_for_status()
        # Tag-only entries (a candidate without traffic) carry no percent: they do not serve.
        return [t["revisionName"] for t in response.json()["status"]["traffic"] if t.get("percent", 0) > 0]

    async def _access_token(self) -> str:
        if time.monotonic() < self._token_expires_at:
            return self._token
        assert self._client is not None
        response = await self._client.get(_METADATA_TOKEN_URL, headers=_METADATA_HEADERS)
        response.raise_for_status()
        body = response.json()
        self._token = body["access_token"]
        self._token_expires_at = time.monotonic() + body["expires_in"] - _TOKEN_MARGIN_SECONDS
        return self._token


def _report_crash(task: asyncio.Task[None]) -> None:
    """Any other error ends the loop: the gate opens for good, #1082 again, and says so."""
    if task.cancelled() or task.exception() is None:
        return
    logger.error("Serving gate stopped on an unexpected error, consuming from now on", exc_info=task.exception())
    serving_gate.consuming = True


serving_gate = ServingGate()
