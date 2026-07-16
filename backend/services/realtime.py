import asyncio
from collections import defaultdict
from typing import Dict, Set

# In-process pub/sub for notification push over SSE. Deliberately in-memory,
# not Redis/a message broker: this backend runs as a single uvicorn process
# (see .claude/launch.json - no --workers flag), matching the project's
# established "no new infrastructure" convention (same reasoning as the
# self-heal-on-read pattern instead of a task scheduler). If this backend is
# ever horizontally scaled across processes/machines, this module is the one
# place that would need to become Redis pub/sub (or similar) - every caller
# goes through subscribe()/unsubscribe()/publish_to_user() below, so that
# swap would not touch any other file.
_subscribers: Dict[str, Set[asyncio.Queue]] = defaultdict(set)


def subscribe(user_id: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    _subscribers[user_id].add(queue)
    return queue


def unsubscribe(user_id: str, queue: asyncio.Queue) -> None:
    subscribers = _subscribers.get(user_id)
    if not subscribers:
        return
    subscribers.discard(queue)
    if not subscribers:
        _subscribers.pop(user_id, None)


def publish_to_user(user_id: str, payload: dict) -> None:
    """Fire-and-forget push to every live connection for this user (usually
    zero or one, but a user open in two tabs gets two). A full queue means a
    stalled/dead consumer - drop rather than block the publisher; the
    client's own REST fetch (on next poll or reconnect) is the source of
    truth, this is purely a low-latency delivery shortcut on top of it."""
    for queue in list(_subscribers.get(user_id, ())):
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            pass
