"""Always-on incremental scanner worker for VPS/Docker/Railway/Render."""
from __future__ import annotations

import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import scanner_engine

INTERVAL = max(30, int(os.getenv("SCAN_INTERVAL_SECONDS", "60")))
BATCH_SIZE = max(20, int(os.getenv("UNIVERSE_BATCH_SIZE", "60")))
DEEP_LIMIT = max(5, int(os.getenv("DEEP_LIMIT", "15")))
WORKERS = max(4, int(os.getenv("WORKERS", "16")))
MIN_ATR = float(os.getenv("MIN_ATR_PCT", "1.8"))
MIN_VOL = float(os.getenv("MIN_VOL_SHOCK", "1.5"))
MIN_RR = float(os.getenv("MIN_RR", "2.0"))
NEWS_GATE = float(os.getenv("NEWS_GATE", "0"))
RUN_FOREVER = os.getenv("RUN_FOREVER", "1") != "0"
OUT = Path(os.getenv("RESULTS_FILE", "runtime/results.json"))
STATE = Path(os.getenv("STATE_FILE", "runtime/scanner_state.json"))
STOP = False


def _stop(*_):
    global STOP
    STOP = True

signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"cursor": 0, "broad_cache": {}, "signals": []}


def save_state(state: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, default=str), encoding="utf-8")


def write_payload(market: dict, universe_count: int, scanned_count: int, signals: pd.DataFrame, status: str, cycle_started: float):
    payload = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "market": market,
        "universe_count": universe_count,
        "scanned_count": scanned_count,
        "signals": signals.to_dict(orient="records") if not signals.empty else [],
        "runtime_seconds": round(time.time() - cycle_started, 2),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, default=str), encoding="utf-8")


def main():
    state = load_state()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    while not STOP:
        started = time.time()
        try:
            universe_df = scanner_engine.nse_universe()
            universe = universe_df["Symbol"].tolist() if not universe_df.empty else scanner_engine.FALLBACK
            n = len(universe)
            cursor = int(state.get("cursor", 0)) % n
            batch = [universe[(cursor + i) % n] for i in range(min(BATCH_SIZE, n))]
            state["cursor"] = (cursor + len(batch)) % n
            mkt = scanner_engine.market()
            retained = {}

            def on_broad(done, total, partial):
                if not partial.empty:
                    for row in partial.to_dict("records"):
                        state["broad_cache"][row["Symbol"]] = row
                rolling = pd.DataFrame(state["broad_cache"].values())
                if not rolling.empty:
                    rolling = rolling.sort_values("BroadScore", ascending=False).reset_index(drop=True)
                write_payload(mkt, n, len(state["broad_cache"]), pd.DataFrame(state.get("signals", [])), f"broad_scan {done}/{total}", started)

            broad_batch = scanner_engine.broad(batch, "NS", workers=WORKERS, on_update=on_broad)
            if not broad_batch.empty:
                for row in broad_batch.to_dict("records"):
                    state["broad_cache"][row["Symbol"]] = row
            rolling = pd.DataFrame(state["broad_cache"].values())
            if rolling.empty:
                write_payload(mkt, n, 0, pd.DataFrame(), "waiting_for_market_data", started)
            else:
                rolling = rolling.sort_values("BroadScore", ascending=False).reset_index(drop=True)
                candidates = rolling.head(DEEP_LIMIT).copy()

                def on_deep(done, total, partial):
                    if not partial.empty:
                        state["signals"] = partial.to_dict(orient="records")
                    current = pd.DataFrame(state.get("signals", []))
                    write_payload(mkt, n, len(state["broad_cache"]), current, f"deep_scan {done}/{total}", started)
                    if not partial.empty:
                        best = partial.iloc[0]
                        print(f"SELECTED {best['Symbol']} {best['Direction']} reliability={best['Reliability']}", flush=True)

                final = scanner_engine.deep(candidates, mkt, MIN_ATR, MIN_VOL, MIN_RR, NEWS_GATE, workers=max(4, WORKERS // 2), on_update=on_deep)
                if not final.empty:
                    state["signals"] = final.to_dict(orient="records")
                write_payload(mkt, n, len(state["broad_cache"]), final if not final.empty else pd.DataFrame(state.get("signals", [])), "cycle_complete", started)
            save_state(state)
            print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} batch={len(batch)} universe={n} retained={len(state['broad_cache'])} signals={len(state.get('signals', []))}", flush=True)
        except Exception as exc:
            print(f"SCAN ERROR: {exc}", flush=True)
        if not RUN_FOREVER:
            break
        elapsed = time.time() - started
        time.sleep(max(5, INTERVAL - elapsed))


if __name__ == "__main__":
    main()
