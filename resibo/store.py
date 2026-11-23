"""In-memory TTL batch store: thread-safe, expired batches purged on every access, LRU cap."""

import threading
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime

from resibo.models import Batch, ErrorCode, ResiboError

MAX_BATCHES = 500


class BatchStore:
    def __init__(
        self,
        clock: Callable[[], datetime] | None = None,
        capacity: int = MAX_BATCHES,
    ):
        self._clock = clock or (lambda: datetime.now(UTC))
        self._capacity = capacity
        self._items: OrderedDict[str, Batch] = OrderedDict()
        self._lock = threading.Lock()

    def _purge(self) -> None:
        now = self._clock()
        for batch_id in [b for b, batch in self._items.items() if batch.expires_at <= now]:
            del self._items[batch_id]

    def put(self, batch: Batch) -> None:
        with self._lock:
            self._purge()
            self._items[batch.batch_id] = batch
            self._items.move_to_end(batch.batch_id)
            while len(self._items) > self._capacity:
                self._items.popitem(last=False)  # evict the least recently used

    def get(self, batch_id: str) -> Batch:
        with self._lock:
            self._purge()
            batch = self._items.get(batch_id)
            if batch is None:
                raise ResiboError(
                    ErrorCode.BATCH_NOT_FOUND,
                    "This result has expired or does not exist. Run the extraction again.",
                )
            self._items.move_to_end(batch_id)
            return batch

    def contains(self, batch_id: str) -> bool:
        with self._lock:
            self._purge()
            return batch_id in self._items

    def __len__(self) -> int:
        with self._lock:
            self._purge()
            return len(self._items)
