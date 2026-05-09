"""
RAGAS watchdog — background asyncio task that monitors evaluation scores.

Polls Redis rolling window every RAGAS_WATCHDOG_INTERVAL_SECONDS seconds.
Sends a Slack alert if context_relevance drops below RAGAS_RELEVANCE_ALERT_THRESHOLD.
Also writes a row to the audit_log table so every alert is auditable.

Error handling contract:
  - The Slack HTTP call is wrapped in try/except. If Slack is down or the
    webhook URL is misconfigured, the failure is logged at ERROR level but
    does NOT cancel the watchdog task.
  - An asyncio.Task done-callback is attached to log any unhandled exceptions
    that escape the main loop (defensive — should not happen in practice).
  - On first poll, if sample_count < RAGAS_WATCHDOG_MIN_SAMPLES, the alert
    is skipped. This prevents false positives during application startup when
    the Redis window has fewer than N data points.

Cooldown:
  A Redis key 'ragas:alert_cooldown' is set after each alert with a TTL of
  RAGAS_WATCHDOG_INTERVAL_SECONDS. If the key exists on the next poll,
  the alert is suppressed — one alert per interval maximum.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time

import httpx
import structlog

logger = structlog.get_logger(__name__)

INTERVAL: int = int(os.getenv("RAGAS_WATCHDOG_INTERVAL_SECONDS", "600"))
THRESHOLD: float = float(os.getenv("RAGAS_RELEVANCE_ALERT_THRESHOLD", "0.7"))
MIN_SAMPLES: int = int(os.getenv("RAGAS_WATCHDOG_MIN_SAMPLES", "10"))
SLACK_WEBHOOK: str = os.getenv("SLACK_WEBHOOK_URL", "")
COOLDOWN_KEY = "ragas:alert_cooldown"


def _get_redis():
    try:
        import redis
        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        return redis.from_url(url)
    except Exception:
        return None


async def _send_slack_alert(avg_relevance: float, sample_count: int) -> None:
    """
    POST a Slack webhook message. Wrapped in try/except — Slack being down
    must not kill the watchdog. Error logged at ERROR level.
    """
    if not SLACK_WEBHOOK:
        logger.warning("SLACK_WEBHOOK_URL not set — alert suppressed")
        return

    payload = {
        "text": (
            f":rotating_light: *RAGAS Alert*\n"
            f"*context_relevance* dropped below threshold.\n"
            f"Current average: `{avg_relevance:.3f}` "
            f"(threshold: `{THRESHOLD}`, samples: `{sample_count}`)\n"
            f"Check Phoenix at http://localhost:{os.getenv('PHOENIX_PORT', '6006')}"
        )
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(SLACK_WEBHOOK, json=payload)
            resp.raise_for_status()
        logger.info("Slack alert sent", avg_relevance=avg_relevance)
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Slack webhook HTTP error",
            status_code=exc.response.status_code,
            detail=exc.response.text[:200],
        )
    except httpx.RequestError as exc:
        logger.error("Slack webhook request failed", error=str(exc))
    except Exception as exc:
        logger.error("Unexpected Slack alert error", error=str(exc))


async def _write_audit_alert(avg_relevance: float, sample_count: int, db_pool) -> None:
    """Write an alert event to audit_log. Silently skips if db_pool is None."""
    if db_pool is None:
        return
    try:
        from audit import record
        await record(
            db_pool,
            token={"sub": "system:watchdog", "role": "system"},
            action="ragas_alert",
            resource="ragas:context_relevance",
            status="error",
            detail={
                "avg_relevance": avg_relevance,
                "threshold": THRESHOLD,
                "sample_count": sample_count,
            },
        )
    except Exception as exc:
        logger.warning("Could not write alert to audit_log: %s", exc)


async def _watchdog_loop(db_pool) -> None:
    """Main watchdog polling loop. Runs forever until the task is cancelled."""
    logger.info(
        "RAGAS watchdog started",
        interval_seconds=INTERVAL,
        threshold=THRESHOLD,
        min_samples=MIN_SAMPLES,
    )

    while True:
        await asyncio.sleep(INTERVAL)

        try:
            from rag_eval import get_rolling_averages
            stats = get_rolling_averages()
            avg_relevance = stats["context_relevance"]
            sample_count = stats["sample_count"]

            logger.info(
                "Watchdog poll",
                avg_context_relevance=avg_relevance,
                avg_faithfulness=stats["faithfulness"],
                sample_count=sample_count,
            )

            # First-poll guard: skip if window is too thin
            if sample_count < MIN_SAMPLES:
                logger.info(
                    "Watchdog skipping alert: insufficient samples",
                    sample_count=sample_count,
                    required=MIN_SAMPLES,
                )
                continue

            # Cooldown guard: skip if an alert was sent recently
            r = _get_redis()
            if r is not None and r.exists(COOLDOWN_KEY):
                logger.info("Watchdog alert suppressed by cooldown")
                continue

            # Alert condition
            if avg_relevance < THRESHOLD:
                logger.warning(
                    "RAGAS alert triggered",
                    avg_relevance=avg_relevance,
                    threshold=THRESHOLD,
                )
                await _send_slack_alert(avg_relevance, sample_count)
                await _write_audit_alert(avg_relevance, sample_count, db_pool)

                # Set cooldown TTL = interval so at most one alert per window
                if r is not None:
                    try:
                        r.setex(COOLDOWN_KEY, INTERVAL, "1")
                    except Exception:
                        pass

        except asyncio.CancelledError:
            logger.info("RAGAS watchdog cancelled")
            raise
        except Exception as exc:
            # Log but never crash the loop
            logger.error("Watchdog poll failed unexpectedly", error=str(exc))


def _on_watchdog_done(task: asyncio.Task) -> None:
    """
    Done-callback attached to the watchdog Task.
    asyncio silently swallows exceptions in Tasks without done-callbacks.
    This callback ensures any unexpected exception is always logged.
    """
    if task.cancelled():
        logger.info("RAGAS watchdog task was cancelled")
        return
    exc = task.exception()
    if exc is not None:
        logger.error("RAGAS watchdog task raised an unhandled exception", error=str(exc))


def start_watchdog(db_pool=None) -> asyncio.Task:
    """
    Start the watchdog as a background asyncio Task.

    Call this from the FastAPI lifespan (after the event loop is running).
    The done-callback is attached automatically.

    Args:
        db_pool: asyncpg pool for audit_log writes. May be None in dev.

    Returns:
        The asyncio.Task — store a reference to prevent garbage collection.
    """
    loop = asyncio.get_event_loop()
    task = loop.create_task(_watchdog_loop(db_pool), name="ragas_watchdog")
    task.add_done_callback(_on_watchdog_done)
    return task
