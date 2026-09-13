"""Long-running scanner service for VPS/Render/Railway/Docker.

It continuously refreshes the universe, market regime and ranked candidates.
It does not place broker orders.
"""
from __future__ import annotations

import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

import scanner_engine

INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "60"))
RUN_FOREVER = os.getenv("RUN_FOREVER", "1") != "0"
OUT = Path(os.getenv("RESULTS_FILE", "runtime/results.json"))
STOP = False


def _stop(*_):
    global STOP
    STOP = True

signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    while not STOP:
        started = time.time()
        try:
            universe = scanner_engine.fetch_nse_universe()
            symbols = universe["Symbol"].tolist() if not universe.empty else scanner_engine.FALLBACK_NSE
            broad = scanner_engine.broad_daily_candidates(symbols, "NS", workers=16)
            market = scanner_engine.market_snapshot()
            shortlist = scanner_engine.deep_scan(broad.head(250), market, 1.5, 1.3, 2.0, 0.0, workers=12)
            payload = {
                "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "market": market,
                "universe_count": len(symbols),
                "candidate_count": len(broad),
                "signals": shortlist.to_dict(orient="records") if not shortlist.empty else [],
                "runtime_seconds": round(time.time() - started, 2),
            }
            OUT.write_text(json.dumps(payload, default=str), encoding="utf-8")
            print(f"{payload['updated']} universe={len(symbols)} candidates={len(broad)} signals={len(payload['signals'])}", flush=True)
        except Exception as exc:
            print(f"SCAN ERROR: {exc}", flush=True)
        if not RUN_FOREVER:
            break
        time.sleep(max(10, INTERVAL))


if __name__ == "__main__":
    main()
