import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from datetime import timedelta

from sqlalchemy import text

from src.repositories.store import now_utc
from src.services.login_reset import ResetAttempt


def reserve_usage(store, subject: str, operation: str, maximum: int) -> bool:
    now = now_utc()
    bucket = hashlib.sha256(f"usage:{operation}:{subject}".encode()).hexdigest()
    with store.session() as session:
        session.execute(text("SELECT pg_advisory_xact_lock(1296649481)"))
        row = session.get(ResetAttempt, bucket)
        if row is None:
            row = ResetAttempt(bucket=bucket, window_start=now, attempts=0)
            session.add(row)
        if now - row.window_start >= timedelta(minutes=10):
            row.window_start = now
            row.attempts = 0
        if row.attempts >= maximum:
            return False
        row.attempts += 1
    return True


def reserve_ai_ingress(store, address, settings):
    now = now_utc()
    budgets = (("global", settings.ai_ingress_requests_per_10_minutes),
               (hashlib.sha256(address.encode()).hexdigest(), settings.ai_ip_requests_per_10_minutes))
    with store.session_factory.begin() as session:
        session.execute(text("SELECT pg_advisory_xact_lock(1296649483)"))
        updates = []
        for identity, maximum in budgets:
            bucket = hashlib.sha256(f"ai-ingress:{identity}".encode()).hexdigest()
            row = session.get(ResetAttempt, bucket)
            if row is None:
                row = ResetAttempt(bucket=bucket, window_start=now, attempts=0)
                session.add(row)
            if now - row.window_start >= timedelta(minutes=10):
                row.window_start, row.attempts = now, 0
            if row.attempts >= maximum:
                return max(1, int((row.window_start + timedelta(minutes=10) - now).total_seconds()) + 1)
            updates.append(row)
        for row in updates:
            row.attempts += 1
    return 0


class PrivateResultCache:
    def __init__(self, max_bytes: int = 20 * 1024 * 1024, ttl: int = 600):
        self.max_bytes = max_bytes
        self.ttl = ttl
        self.entries = OrderedDict()
        self.size = 0
        self.pending = {}

    async def load(self, key, loader, disconnected=None):
        cached = self.get(key)
        if cached is not None:
            return cached
        entry = self.pending.get(key)
        if entry is None:
            async def prepare():
                value = await loader()
                self.put(key, value)
                return value

            entry = [asyncio.create_task(prepare()), 0]
            self.pending[key] = entry
        entry[1] += 1
        try:
            while not entry[0].done():
                await asyncio.wait({entry[0]}, timeout=0.2)
                if disconnected and await disconnected():
                    raise asyncio.CancelledError()
            return entry[0].result()
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                if not entry[0].done():
                    entry[0].cancel()
                await asyncio.gather(entry[0], return_exceptions=True)
                if self.pending.get(key) is entry:
                    self.pending.pop(key, None)

    def get(self, key):
        for expired in [item for item, entry in self.entries.items() if time.monotonic() - entry[0] >= self.ttl]:
            self.size -= self.entries.pop(expired)[2]
        entry = self.entries.get(key)
        if entry:
            self.entries.move_to_end(key)
            return entry[1]
        return None

    def put(self, key, value):
        size = len(value) if isinstance(value, bytes) else len(json.dumps(value, ensure_ascii=False).encode())
        if size > self.max_bytes:
            return
        if key in self.entries:
            self.size -= self.entries.pop(key)[2]
        while self.entries and (self.size + size > self.max_bytes or len(self.entries) >= 64):
            self.size -= self.entries.popitem(last=False)[1][2]
        self.entries[key] = (time.monotonic(), value, size)
        self.size += size

    def clear(self):
        for task, _ in self.pending.values():
            task.cancel()
        self.pending.clear()
        self.entries.clear()
        self.size = 0
