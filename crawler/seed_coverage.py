# Bounded retries for configured seed URLs missing from the current index.
from __future__ import annotations

import math
from collections.abc import Callable, Iterable, MutableMapping

DEFAULT_SEED_RETRY_SECONDS = 24 * 60 * 60


def schedule_missing_seed_urls(
    seeds: Iterable[str],
    indexed_hosts: Iterable[str],
    queued_urls: Iterable[str],
    retry_after: MutableMapping[str, int | float],
    *,
    now: float,
    cooldown_seconds: int = DEFAULT_SEED_RETRY_SECONDS,
    queue_limit: int = 50_000,
    host_key: Callable[[str], str],
) -> list[tuple[str, int]]:
    # Return missing seed URLs without bypassing queue or retry bounds.
    # The caller still applies robots.txt and per-host rate limits before fetching.
    if not callable(host_key):
        raise TypeError("host_key must be callable")
    if not isinstance(now, (int, float)) or not math.isfinite(float(now)):
        raise ValueError("now must be a finite timestamp")
    if not isinstance(cooldown_seconds, int) or cooldown_seconds < 0:
        raise ValueError("cooldown_seconds must be a non-negative integer")
    if not isinstance(queue_limit, int) or queue_limit < 0:
        raise ValueError("queue_limit must be a non-negative integer")

    represented = set(indexed_hosts or ())
    queued = {str(url) for url in (queued_urls or ())}
    scheduled: list[tuple[str, int]] = []
    retry_at = float(now) + cooldown_seconds

    for raw_seed in seeds:
        seed = str(raw_seed).strip()
        if not seed:
            continue
        host = host_key(seed)
        if host in represented:
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
            continue
        if len(queued) + len(scheduled) >= queue_limit:
            break

        scheduled.append((seed, 0))
        queued.add(seed)
        retry_after[seed] = int(retry_at)

    return scheduled
