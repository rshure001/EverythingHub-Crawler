# Bounded retries for configured seed URLs missing from the current index.
from __future__ import annotations

import math
from collections.abc import Iterable, MutableMapping

DEFAULT_SEED_RETRY_SECONDS = 10 * 60


def schedule_missing_seed_urls(
    seeds: Iterable[str],
    indexed_urls: Iterable[str],
    queued_urls: Iterable[str],
    retry_after: MutableMapping[str, int | float],
    *,
    now: float,
    cooldown_seconds: int = DEFAULT_SEED_RETRY_SECONDS,
    queue_limit: int = 50_000,
) -> list[tuple[str, int]]:
    """Return exact seed URLs not yet indexed without skipping sibling paths.

    Host-level politeness is enforced by the crawler's shared host scheduler.
    Seed coverage itself must be URL-scoped: an indexed homepage does not prove
    that a separately configured visitor, directory, or catalog path was read.
    """
    if not isinstance(now, (int, float)) or not math.isfinite(float(now)):
        raise ValueError("now must be a finite timestamp")
    if not isinstance(cooldown_seconds, int) or cooldown_seconds < 0:
        raise ValueError("cooldown_seconds must be a non-negative integer")
    if not isinstance(queue_limit, int) or queue_limit < 0:
        raise ValueError("queue_limit must be a non-negative integer")

    represented = {str(url) for url in (indexed_urls or ())}
    queued = {str(url) for url in (queued_urls or ())}
    scheduled: list[tuple[str, int]] = []
    retry_at = float(now) + cooldown_seconds

    for raw_seed in seeds:
        seed = str(raw_seed).strip()
        if not seed:
            continue
        if seed in represented:
            retry_after.pop(seed, None)
            continue

        if seed in queued:
            try:
                previous_retry = float(retry_after.get(seed, 0) or 0)
            except (TypeError, ValueError, OverflowError):
                previous_retry = 0
            if previous_retry <= now:
                retry_after[seed] = int(retry_at)
            continue

        try:
            next_retry = float(retry_after.get(seed, 0) or 0)
        except (TypeError, ValueError, OverflowError):
            next_retry = 0
        if next_retry > now:
            # Clamp old long cooldowns to the current bounded retry window.
            retry_after[seed] = int(min(next_retry, retry_at))
            continue
        if len(queued) + len(scheduled) >= queue_limit:
            break

        scheduled.append((seed, 0))
        queued.add(seed)
        retry_after[seed] = int(retry_at)

    return scheduled
