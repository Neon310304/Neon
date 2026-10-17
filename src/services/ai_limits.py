import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta
from time import perf_counter
from uuid import uuid4

from sqlalchemy import DateTime, Integer, String, func, select, text
from sqlalchemy.orm import Mapped, mapped_column

from src.repositories.store import Base, now_utc

ai_operation = ContextVar("ai_operation", default="llm")
logger = logging.getLogger("moveinmate.ai_usage")


@contextmanager
def ai_category(operation):
    token = ai_operation.set(operation)
    try:
        yield
    finally:
        ai_operation.reset(token)


class AiUsageBucket(Base):
    __tablename__ = "ai_usage_buckets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[int] = mapped_column(Integer)


class AiRequestLease(Base):
    __tablename__ = "ai_request_leases"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    operation: Mapped[str] = mapped_column(String(12), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AiLimitError(RuntimeError):
    def __init__(self, code="ai_global_limit_reached", retry_after=600):
        self.code = code
        self.retry_after = retry_after
        self.message = "Hệ thống đang đạt giới hạn AI. Bạn vẫn có thể xem và xử lý ticket; vui lòng thử lại sau."
        super().__init__(self.message)


class AiUsageGate:
    def __init__(self, store, settings):
        self.store = store
        self.settings = settings
        self._active_call = ContextVar(f"ai_call_{id(self)}", default=None)

    def _reserve(self, operation, characters):
        now = now_utc()
        lease_id = uuid4().hex
        with self.store.session_factory.begin() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(1296649482)"))
            session.query(AiRequestLease).filter(AiRequestLease.expires_at <= now).delete()
            total = session.scalar(select(func.count()).select_from(AiRequestLease))
            concurrent = session.scalar(select(func.count()).select_from(AiRequestLease).where(AiRequestLease.operation == operation))
            if total >= self.settings.ai_max_concurrent or concurrent >= getattr(self.settings, f"ai_{operation}_max_concurrent"):
                raise AiLimitError("ai_concurrency_limit_reached", 2)
            requested = [("all", self.settings.ai_requests_per_10_minutes, 1),
                         (operation, getattr(self.settings, f"ai_{operation}_requests_per_10_minutes"), 1)]
            if operation == "tts":
                requested.append(("tts_chars", self.settings.ai_tts_chars_per_10_minutes, characters))
            updates = []
            for name, maximum, amount in requested:
                bucket_id = hashlib.sha256(f"ai-global:{name}".encode()).hexdigest()
                bucket = session.get(AiUsageBucket, bucket_id)
                if bucket is None:
                    bucket = AiUsageBucket(id=bucket_id, window_start=now, used=0)
                    session.add(bucket)
                if now - bucket.window_start >= timedelta(minutes=10):
                    bucket.window_start, bucket.used = now, 0
                if bucket.used + amount > maximum:
                    remaining = max(1, int((bucket.window_start + timedelta(minutes=10) - now).total_seconds()) + 1)
                    raise AiLimitError(retry_after=remaining)
                updates.append((bucket, amount))
            for bucket, amount in updates:
                bucket.used += amount
            session.add(AiRequestLease(id=lease_id, operation=operation, expires_at=now + timedelta(seconds=90)))
        return lease_id

    def _release(self, lease_id):
        with self.store.session_factory.begin() as session:
            session.query(AiRequestLease).filter_by(id=lease_id).delete()

    def _renew(self, lease_id):
        with self.store.session_factory.begin() as session:
            session.query(AiRequestLease).filter_by(id=lease_id).update({"expires_at": now_utc() + timedelta(seconds=90)})

    async def _heartbeat(self, lease_id):
        while True:
            await asyncio.sleep(20)
            await asyncio.to_thread(self._renew, lease_id)

    @asynccontextmanager
    async def call(self, operation, characters=0):
        if operation not in {"llm", "tts", "stt", "ocr"} or characters < 0:
            raise ValueError("invalid_ai_operation")
        scope = (operation, asyncio.current_task())
        if self._active_call.get() == scope:
            yield
            return
        reservation = asyncio.create_task(asyncio.to_thread(self._reserve, operation, characters))
        try:
            lease_id = await asyncio.shield(reservation)
        except asyncio.CancelledError:
            try:
                lease_id = await reservation
                await asyncio.shield(asyncio.to_thread(self._release, lease_id))
            except AiLimitError:
                pass
            raise
        active = self._active_call.set(scope)
        heartbeat = asyncio.create_task(self._heartbeat(lease_id))
        started = perf_counter()
        outcome = "success"
        try:
            yield
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except TimeoutError:
            outcome = "timeout"
            raise
        except Exception:
            outcome = "error"
            raise
        finally:
            self._active_call.reset(active)
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            await asyncio.shield(asyncio.to_thread(self._release, lease_id))
            logger.info("ai_provider operation=%s outcome=%s duration_ms=%.2f", operation, outcome, (perf_counter() - started) * 1000)
