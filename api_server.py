from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

APP_DIR = Path(__file__).resolve().parent
RESULTS_FILE = Path(os.getenv("RESULTS_FILE", "/app/runtime/results.json"))
MAX_RESULT_AGE = max(30, int(os.getenv("RESULTS_STALE_SECONDS", "180")))
START_WORKER = os.getenv("START_WORKER", "1") != "0"
WORKER_CMD = [sys.executable, str(APP_DIR / "worker.py")]

app = FastAPI(title="Indian Intraday Alpha Scanner API", version="1.0")
_worker: subprocess.Popen[str] | None = None
_lock = threading.Lock()
_stop = False


def _read_results() -> dict:
    try:
        return json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _result_age(payload: dict) -> float | None:
    updated = payload.get("updated")
    if not updated:
        return None
    try:
        dt = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
    except Exception:
        return None


def _start_worker() -> None:
    global _worker
    with _lock:
        if not START_WORKER:
            return
        if _worker is not None and _worker.poll() is None:
            return
        RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        _worker = subprocess.Popen(
            WORKER_CMD,
            cwd=str(APP_DIR),
            env=env,
            stdout=None,
            stderr=None,
            text=True,
        )


def _watch_worker() -> None:
    while not _stop:
        try:
            _start_worker()
        except Exception as exc:
            print(f"WORKER START ERROR: {exc}", flush=True)
        time.sleep(10)


def _shutdown() -> None:
    global _stop
    _stop = True
    with _lock:
        if _worker is not None and _worker.poll() is None:
            try:
                _worker.terminate()
                _worker.wait(timeout=15)
            except Exception:
                try:
                    _worker.kill()
                except Exception:
                    pass


@app.on_event("startup")
def startup() -> None:
    _start_worker()
    threading.Thread(target=_watch_worker, daemon=True, name="worker-watchdog").start()


@app.on_event("shutdown")
def shutdown() -> None:
    _shutdown()


atexit.register(_shutdown)


@app.get("/")
def root() -> dict:
    return {"service": "indian-intraday-alpha-scanner", "status": "ok", "results": "/results", "health": "/health"}


@app.get("/health")
def health():
    payload = _read_results()
    age = _result_age(payload)
    worker_running = _worker is not None and _worker.poll() is None
    data_fresh = age is not None and age <= MAX_RESULT_AGE
    status = "ok" if worker_running else "worker_offline"
    code = 200 if worker_running else 503
    return JSONResponse(
        status_code=code,
        content={
            "status": status,
            "worker_running": worker_running,
            "worker_pid": _worker.pid if _worker is not None else None,
            "results_available": bool(payload),
            "results_fresh": data_fresh,
            "result_age_seconds": age,
            "updated": payload.get("updated"),
        },
    )


@app.get("/results")
def results():
    payload = _read_results()
    if not payload:
        return JSONResponse(status_code=503, content={"status": "NO_RESULT", "message": "Worker has not produced results yet."})
    age = _result_age(payload)
    payload["api_fresh"] = age is not None and age <= MAX_RESULT_AGE
    payload["result_age_seconds"] = age
    if age is None or age > MAX_RESULT_AGE:
        payload["status"] = "STALE_RESULTS"
    return JSONResponse(content=payload)
