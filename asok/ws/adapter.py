from __future__ import annotations

import json
import logging
import threading
from typing import Any, Optional, Union

logger = logging.getLogger("asok.ws.adapter")


class BaseWebSocketAdapter:
    """Base interface for WebSocket distributed cluster adapters."""

    def __init__(self) -> None:
        self.server: Any = None

    def bind(self, server: Any) -> None:
        self.server = server

    def publish(self, channel: str, message: Union[str, bytes]) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class InMemoryWebSocketAdapter(BaseWebSocketAdapter):
    """Local in-memory adapter for standalone single-server deployments."""

    def publish(self, channel: str, message: Union[str, bytes]) -> None:
        # Standalone server delivers directly to local connections.
        pass


def _to_str(val: Any) -> str:
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return str(val or "")


class RedisWebSocketAdapter(BaseWebSocketAdapter):
    """Distributed Redis Pub/Sub adapter for horizontal multi-server clustering."""

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        channel_prefix: str = "asok:ws",
    ) -> None:
        super().__init__()
        import uuid

        self.node_id = uuid.uuid4().hex
        self.redis_url = redis_url
        self.channel_prefix = channel_prefix
        self._pub_client: Any = None
        self._sub_client: Any = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def _get_pub_client(self) -> Any:
        if self._pub_client is not None:
            return self._pub_client
        try:
            import redis

            self._pub_client = redis.Redis.from_url(self.redis_url)
            return self._pub_client
        except Exception as e:
            logger.warning("Could not initialize Redis pub client: %s", e)
            return None

    def bind(self, server: Any) -> None:
        super().bind(server)
        self._start_subscriber()

    def _start_subscriber(self) -> None:
        try:
            import redis

            self._sub_client = redis.Redis.from_url(self.redis_url)
            self._running = True
            self._thread = threading.Thread(
                target=self._listen_loop, daemon=True, name="asok-ws-redis-sub"
            )
            self._thread.start()
        except Exception as e:
            logger.warning("Could not connect Redis pub/sub subscriber: %s", e)

    def _listen_loop(self) -> None:
        try:
            pubsub = self._sub_client.pubsub()
            pubsub.psubscribe(f"{self.channel_prefix}:*")
            for item in pubsub.listen():
                if not self._running:
                    break
                self._dispatch_item(item)
        except Exception as e:
            logger.debug("Redis pub/sub subscriber loop stopped: %s", e)

    def _dispatch_item(self, item: dict[str, Any]) -> None:
        if not item or item.get("type") != "pmessage":
            return
        channel = _to_str(item.get("channel", b""))
        data = _to_str(item.get("data", b""))
        self._handle_incoming_envelope(channel, data)

    def _parse_incoming_payload(self, raw_payload: str) -> Optional[dict[str, Any]]:
        try:
            payload = json.loads(raw_payload)
            if isinstance(payload, dict) and payload.get("node_id") != self.node_id:
                return payload
        except Exception as e:
            logger.debug("Error decoding Redis envelope JSON: %s", e)
        return None

    def _deliver_envelope(self, payload: dict[str, Any]) -> None:
        target = payload.get("target")
        name = payload.get("name")
        msg = payload.get("msg")
        if not isinstance(name, str) or msg is None:
            return
        if target == "room":
            self.server.broadcast_to(name, msg, from_adapter=True)
        elif target == "path":
            self.server.broadcast(name, msg, from_adapter=True)

    def _handle_incoming_envelope(self, channel: str, raw_payload: str) -> None:
        if not self.server:
            return
        payload = self._parse_incoming_payload(raw_payload)
        if payload is not None:
            self._deliver_envelope(payload)

    def publish(self, channel: str, message: Union[str, bytes]) -> None:
        client = self._get_pub_client()
        if not client:
            return
        msg_str = (
            message.decode("utf-8", errors="replace")
            if isinstance(message, bytes)
            else str(message)
        )
        target, name = self._parse_channel(channel)
        envelope = json.dumps(
            {
                "target": target,
                "name": name,
                "msg": msg_str,
                "node_id": self.node_id,
            }
        )
        redis_channel = f"{self.channel_prefix}:{channel}"
        try:
            client.publish(redis_channel, envelope)
        except Exception as e:
            logger.warning("Failed to publish message to Redis: %s", e)

    @staticmethod
    def _parse_channel(channel: str) -> tuple[str, str]:
        if ":" in channel:
            parts = channel.split(":", 1)
            return parts[0], parts[1]
        return "path", channel

    def close(self) -> None:
        self._running = False
        if self._pub_client:
            try:
                self._pub_client.close()
            except Exception:
                pass
        if self._sub_client:
            try:
                self._sub_client.close()
            except Exception:
                pass
