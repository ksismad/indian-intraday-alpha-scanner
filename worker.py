"""Always-on incremental scanner worker for VPS/Docker/Railway/Render."""
from __future__ import annotations

import json
import os
import signal
import time
from datetime import datetime, time as dt_time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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
BROAD_CACHE_TTL = max(10, int(os.getenv("BROAD_CACHE_TTL_MINUTES", "45")))
RUN_FOREVER = os.getenv("RUN_FOREVER", "1") != "0"
OUT = Path(os.getenv("RESULTS_FILE", "runtime/results.json"))
STATE = Path(os.getenv("STATE_FILE", "runtime/scanner_state.json"))
IST = ZoneInfo("Asia/Kolkata")
TRADING_START = dt_time(9, 30)
TRADING_END = dt_time(15, 30)
STOP = False


def _stop(*_):
    global STOP
    STOP = True


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)


def now_ts() -> float:
    return time.time()


def market_phase() -> str:
    local = datetime.now(IST)
    if local.weekday() >= 5:
        return "WEEKEND"
    if local.time() < TRADING_START:
        return "OPENING_WINDOW"
    if local.time() > TRADING_END:
        return "CLOSED"
    return "LIVE"


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"cursor": 0, "broad_cache": {}, "signals": []}


def save_state(state: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, default=str), encoding="utf-8")


def prune_broad_cache(state: dict):
    cutoff = now_ts() - BROAD_CACHE_TTL * 60
    state["broad_cache"] = {
        symbol: row
        for symbol, row in state.get("broad_cache", {}).items()
        if float(row.get("ScannedUnix", 0) or 0) >= cutoff
    }


def write_payload(market: dict, universe_count: int, scanned_count: int, signals: pd.DataFrame, status: str, cycle_started: float):
    payload = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "market_phase_ist": market_phase(),
        "market": market,
        "universe_count": universe_count,
        "scanned_count": scanned_count,
        "signals": signals.to_dict(orient="records") if not signals.empty else [],
        "runtime_seconds": round(time.time() - cycle_started, 2),
        "signal_policy": "No synthetic/fallback market data; signals only qualify inside 09:30-15:30 IST and expire between validation cycles.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(OUT.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, default=str), encoding="utf-8")
    tmp.replace(OUT)


def main():
    state = load_state()
    state.setdefault("broad_cache", {})
    state.setdefault("signals", [])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    while not STOP:
        started = time.time()
        try:
            universe_df = scanner_engine.nse_universe()
            if universe_df.empty:
                write_payload({}, 0, 0, pd.DataFrame(), "NO_UNIVERSE_DATA", started)
                raise RuntimeError("Current NSE EQ security master unavailable")

            universe = universe_df["Symbol"].tolist()
            n = len(universe)
            cursor = int(state.get("cursor", 0)) % n
            batch = [universe[(cursor + i) % n] for i in range(min(BATCH_SIZE, n))]
            state["cursor"] = (cursor + len(batch)) % n
            prune_broad_cache(state)

            mkt = scanner_engine.market()
            phase = market_phase()
            if mkt.get("regime") == "NO_DATA":
                state["signals"] = []
                write_payload(mkt, n, len(state["broad_cache"]), pd.DataFrame(), "NO_MARKET_DATA", started)
            elif phase != "LIVE":
                # Keep the worker alive and maintaining the rolling broad cache, but never emit
                # a trade signal during the first 15 minutes, after the session, or weekends.
                state["signals"] = []
                write_payload(mkt, n, len(state["broad_cache"]), pd.DataFrame(), f"{phase}_NO_SIGNALS", started)
            else:
                def on_broad(done, total, partial):
                    if not partial.empty:
                        for row in partial.to_dict("records"):
                            row["ScannedUnix"] = now_ts()
                            state["broad_cache"][row["Symbol"]] = row
                    prune_broad_cache(state)
                    write_payload(mkt, n, len(state["broad_cache"]), pd.DataFrame(), f"broad_scan {done}/{total}", started)

                broad_batch = scanner_engine.broad(batch, "NS", workers=WORKERS, on_update=on_broad)
                if not broad_batch.empty:
                    for row in broad_batch.to_dict("records"):
                        row["ScannedUnix"] = now_ts()
                        state["broad_cache"][row["Symbol"]] = row
                prune_broad_cache(state)
                rolling = pd.DataFrame(state["broad_cache"].values())

                if rolling.empty:
                    state["signals"] = []
                    write_payload(mkt, n, 0, pd.DataFrame(), "WAITING_FOR_MARKET_DATA", started)
                else:
                    rolling = rolling.sort_values("BroadScore", ascending=False).reset_index(drop=True)
                    candidates = rolling.head(DEEP_LIMIT).copy()
                    state["signals"] = []

                    def on_deep(done, total, partial):
                        current = partial.sort_values(["Reliability", "R:R"], ascending=False).reset_index(drop=True) if not partial.empty else pd.DataFrame()
                        write_payload(mkt, n, len(state["broad_cache"]), current, f"deep_scan {done}/{total}", started)
                        if not current.empty:
                            best = current.iloc[0]
                            print(f"SELECTED {best['Symbol']} {best['Direction']} reliability={best['Reliability']}", flush=True)

                    final = scanner_engine.deep(
                        candidates,
                        mkt,
                        MIN_ATR,
                        MIN_VOL,
                        MIN_RR,
                        NEWS_GATE,
                        workers=max(4, WORKERS // 2),
                        on_update=on_deep,
                    )
                    state["signals"] = final.to_dict(orient="records") if not final.empty else []
                    write_payload(
                        mkt,
                        n,
                        len(state["broad_cache"]),
                        final,
                        "CYCLE_COMPLETE_WITH_SIGNALS" if not final.empty else "CYCLE_COMPLETE_NO_VALID_SIGNALS",
                        started,
                    )

            save_state(state)
            print(
                f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} "
                f"phase={phase} batch={len(batch)} universe={n} retained={len(state['broad_cache'])} "
                f"signals={len(state.get('signals', []))}",
                flush=True,
            )
        except Exception as exc:
            print(f"SCAN ERROR: {exc}", flush=True)
            if not OUT.exists():
                write_payload({}, 0, 0, pd.DataFrame(), "WORKER_ERROR", started)
        if not RUN_FOREVER:
            break
        elapsed = time.time() - started
        time.sleep(max(5, INTERVAL - elapsed))


if __name__ == "__main__":
    main()
