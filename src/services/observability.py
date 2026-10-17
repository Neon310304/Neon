import json
import logging
from time import perf_counter
from typing import Any

logger = logging.getLogger("moveinmate.agent")


class AgentEvent:
    def __init__(self, conversation_id: str, actor_id: str, event: str):
        self.payload: dict[str, Any] = {
            "conversation_id": conversation_id,
            "actor_id": actor_id,
            "event": event,
        }
        self.started = perf_counter()

    def emit(self, **values: Any) -> None:
        self.payload.update(values)
        self.payload["latency_ms"] = round((perf_counter() - self.started) * 1000, 2)
        logger.info(json.dumps(self.payload, ensure_ascii=False, default=str))
