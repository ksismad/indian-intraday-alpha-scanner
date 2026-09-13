"""Continuous scanner worker.

Runs an incremental full-universe scan, writes provisional/high-confidence results
as soon as they are found, and then starts the next cycle. It never places orders.
"""
from __future__ import annotations

import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import scanner_engine

INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "60"))
RUN_FOREVER = os.getenv("RUN_FOREVER", "1") != "0"
DEEP_LIMIT = int(os.getenv("DEEP_LIMIT", "250"))
OUT = Path(os.getenv("RESULTS_FILE", "runtime/results.json"))
STOP = False
CURRENT = {"signals": [], "universe_count": 0, "candidate_count": 0, "status": "starting"}


def _stop(*_):
    global STOP
    STOP = True

signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)


def write_payload(market: dict, universe_count: int, candidate_count: int, signals: pd.DataFrame, started: float, status: str):
    payload = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "market": market,
        "universe_count": universe_count,
        "candidate_count": candidate_count,
        "signals": signals.to_dict(orient="records") if not signals.empty else [],
        "runtime_seconds": round(time.time() - started, 2),
    }
    OUT.write_text(json.dumps(payload, default=str), encoding="utf-8")


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    while not STOP:
        started = time.time()
        try:
            universe = scanner_engine.nse_universe()
            symbols = universe["Symbol"].tolist() if not universe.empty else scanner_engine.FALLBACK
            mkt = scanner_engine.market()
            broad_holder = {"df": pd.DataFrame()}

            def on_broad(done, total, partial):
                broad_holder["df"] = partial
                # Keep the latest broad pass observable while the search is still running.
                write_payload(mkt, len(symbols), len(partial), pd.DataFrame(), started, f"broad_scan {done}/{total}")

            broad_df = scanner_engine.broad(symbols, "NS", workers=16, on_update=on_broad)
            candidates = broad_df.head(DEEP_LIMIT).copy()
            latest = {"df": pd.DataFrame()}

            def on_deep(done, total, partial):
                latest["df"] = partial
                write_payload(mkt, len(symbols), len(broad_df), partial, started, f"deep_scan {done}/{total}")
                if not partial.empty:
                    best = partial.iloc[0]
                    print(f"SELECTED {best['Symbol']} {best['Direction']} reliability={best['Reliability']}", flush=True)

            final = scanner_engine.deep(candidates, mkt, 1.5, 1.3, 2.0, 0.0, workers=12, on_update=on_deep)
            write_payload(mkt, len(symbols), len(broad_df), final, started, "cycle_complete")
            print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} universe={len(symbols)} candidates={len(broad_df)} signals={len(final)}", flush=True)
        except Exception as exc:
            print(f"SCAN ERROR: {exc}", flush=True)
        if not RUN_FOREVER:
            break
        time.sleep(max(10, INTERVAL))


if __name__ == "__main__":
    main()
