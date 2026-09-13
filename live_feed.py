"""Optional Upstox V3 real-time market feed adapter.

The adapter is credential-gated: without an access token the application stays in
Public Scan mode. With a valid token it uses Upstox MarketDataStreamerV3 with
reconnect support and keeps the latest ticks in memory.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

try:
    import upstox_client
except Exception:  # optional dependency
    upstox_client = None


class UpstoxLiveFeed:
    def __init__(self, access_token: str):
        self.access_token = access_token.strip()
        self.streamer = None
        self.latest: dict[str, dict[str, Any]] = {}
        self.connected = False
        self.last_message_at: float | None = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        return bool(self.access_token) and upstox_client is not None

    def _extract(self, message: Any) -> dict[str, dict[str, Any]]:
        """Best-effort normalization across SDK message representations."""
        if hasattr(message, "to_dict"):
            message = message.to_dict()
        if not isinstance(message, dict):
            return {}
        feeds = message.get("feeds") or {}
        out = {}
        for key, value in feeds.items():
            if not isinstance(value, dict):
                continue
            ltpc = value.get("ltpc") or {}
            full = value.get("full") or value
            ohlc = (full.get("market_ohlc") or {}).get("ohlc") if isinstance(full, dict) else None
            row = {
                "instrument_key": key,
                "ltp": ltpc.get("ltp"),
                "close": ltpc.get("cp"),
                "ltq": ltpc.get("ltq"),
                "timestamp": ltpc.get("ltt") or message.get("currentTs"),
                "received_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            if ohlc:
                for bar in ohlc:
                    if isinstance(bar, dict) and bar.get("interval") in ("1d", "I1", "I30"):
                        row[f"ohlc_{bar['interval']}"] = bar
            out[key] = row
        return out

    def start(self, instrument_keys: list[str], mode: str = "full") -> None:
        if not self.available():
            raise RuntimeError("Upstox SDK/access token not configured")
        configuration = upstox_client.Configuration()
        configuration.access_token = self.access_token
        client = upstox_client.ApiClient(configuration)
        self.streamer = upstox_client.MarketDataStreamerV3(client, instrument_keys, mode)

        def on_open():
            self.connected = True
            self.streamer.auto_reconnect(True, 5, 0)
            self.streamer.subscribe(instrument_keys, mode)

        def on_close(*_):
            self.connected = False

        def on_message(message):
            parsed = self._extract(message)
            if parsed:
                with self._lock:
                    self.latest.update(parsed)
                    self.last_message_at = time.time()

        def on_error(*_):
            self.connected = False

        self.streamer.on("open", on_open)
        self.streamer.on("close", on_close)
        self.streamer.on("message", on_message)
        self.streamer.on("error", on_error)
        threading.Thread(target=self.streamer.connect, daemon=True, name="upstox-feed").start()

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return dict(self.latest)

    def status(self) -> dict[str, Any]:
        age = None if self.last_message_at is None else round(time.time() - self.last_message_at, 1)
        return {"connected": self.connected, "last_tick_age_seconds": age}

    def stop(self) -> None:
        if self.streamer is not None:
            try:
                self.streamer.disconnect()
            except Exception:
                pass
        self.connected = False
