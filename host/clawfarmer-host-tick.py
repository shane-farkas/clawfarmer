#!/usr/bin/env python3
"""Host-side sensor + photo tick for clawfarmer.

Runs as the openclaw service user via systemd timer. Does the SSH work that
the plant agent's sandboxed cron can't reach, then writes results into the
agent's workspace so it can reason over them.

Usage:
    clawfarmer-host-tick sensors          # read all Pi sensors, update state
    clawfarmer-host-tick photo            # capture photo on Jetson, pull to workspace

Why this exists: OpenClaw's non-default agents run cron in an isolated
sandbox that does not have ssh in its execution environment. The agent's
chat session does, but cron does not. This host-side script bridges the
gap: it runs on the host (where ssh works fine as the openclaw user) and
writes state files into the agent's workspace. The agent's cron jobs then
become pure reasoning over that state.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# Defaults can be overridden via environment (set in the systemd unit).
WORKSPACE = Path(os.getenv(
    "CLAWFARMER_WORKSPACE",
    "/var/lib/openclaw/.openclaw/workspace-plant",
))

PI_HOST = os.getenv("PI_HOST", "clawpi.local")
PI_USER = os.getenv("PI_USER", "pi")
PI_KEY = os.getenv("PI_KEY", "/var/lib/openclaw/.ssh/id_ed25519_plantpi")
PI_VENV_PYTHON = os.getenv("PI_VENV_PYTHON", "~/clawfarmer-venv/bin/python3")

JETSON_HOST = os.getenv("JETSON_HOST", "orin-nano.local")
JETSON_USER = os.getenv("JETSON_USER", "shane")
JETSON_KEY = os.getenv("JETSON_KEY", "/var/lib/openclaw/.ssh/id_ed25519_plantjetson")
JETSON_VENV_PYTHON = os.getenv("JETSON_VENV_PYTHON", "~/clawfarmer-venv/bin/python3")
JETSON_PHOTO_DIR = os.getenv("JETSON_PHOTO_DIR", "/var/lib/clawfarmer/photos")

# Soil calibration — env-override once the probe is calibrated
SOIL_CHANNEL = int(os.getenv("SOIL_CHANNEL", "0"))
SOIL_DRY_RAW = int(os.getenv("SOIL_DRY_RAW", "26000"))
SOIL_WET_RAW = int(os.getenv("SOIL_WET_RAW", "12000"))

STATE_FILE = WORKSPACE / "memory/sensor-state.json"
PHOTOS_DIR = WORKSPACE / "photos"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _today_start() -> str:
    return (
        datetime.now()
        .astimezone()
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .isoformat(timespec="seconds")
    )


def _ssh(key: str, user: str, host: str, remote_cmd: str, timeout: int = 30):
    argv = [
        "/usr/bin/ssh",
        "-i", key,
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"ConnectTimeout={min(timeout, 10)}",
        f"{user}@{host}",
        remote_cmd,
    ]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def _scp(key: str, user: str, host: str, remote_path: str, local_path: Path, timeout: int = 60):
    argv = [
        "/usr/bin/scp",
        "-i", key,
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        f"{user}@{host}:{remote_path}",
        str(local_path),
    ]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def _default_state() -> dict:
    return {
        "version": 1,
        "updated_at": None,
        "readings": {
            "soil_moisture": {"value": None, "unit": "pct_vwc", "at": None, "stale": True},
            "temp_f": {"value": None, "unit": "fahrenheit", "at": None, "stale": True},
            "humidity_pct": {"value": None, "unit": "pct_rh", "at": None, "stale": True},
            "lux": {"value": None, "unit": "lux", "at": None, "stale": True},
        },
        "day_ranges": {
            "soil_moisture": {"min": None, "max": None, "window_start": None},
            "temp_f": {"min": None, "max": None, "window_start": None},
            "humidity_pct": {"min": None, "max": None, "window_start": None},
        },
        "grow_light": {"state": "unknown", "last_toggled_at": None},
        "watering_history": [],
        "last_photo": {"filename": None, "at": None},
        "active_detections": [],
        "last_errors": [],
    }


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return _default_state()
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except json.JSONDecodeError:
        return _default_state()
    # fill missing keys if schema evolved
    base = _default_state()
    for k, v in base.items():
        state.setdefault(k, v)
    return state


def _save_state(state: dict) -> None:
    state["updated_at"] = _now_iso()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    tmp.replace(STATE_FILE)


def _record_error(state: dict, source: str, message: str) -> None:
    state.setdefault("last_errors", []).append({
        "source": source,
        "error": message[:500],
        "at": _now_iso(),
    })
    state["last_errors"] = state["last_errors"][-20:]


def _update_day_range(state: dict, key: str, value: float, today_start: str) -> None:
    dr = state["day_ranges"].setdefault(
        key, {"min": None, "max": None, "window_start": None}
    )
    if dr.get("window_start") != today_start:
        dr["min"] = value
        dr["max"] = value
        dr["window_start"] = today_start
    else:
        dr["min"] = value if dr["min"] is None else min(dr["min"], value)
        dr["max"] = value if dr["max"] is None else max(dr["max"], value)


def cmd_sensors() -> None:
    state = _load_state()
    today = _today_start()

    reads = [
        ("soil", f"{PI_VENV_PYTHON} -m clawfarmer_pi read-soil "
                  f"--channel {SOIL_CHANNEL} "
                  f"--dry-raw {SOIL_DRY_RAW} --wet-raw {SOIL_WET_RAW}"),
        ("bme280", f"{PI_VENV_PYTHON} -m clawfarmer_pi read-bme280"),
        ("lux", f"{PI_VENV_PYTHON} -m clawfarmer_pi read-lux"),
    ]

    for name, cmd in reads:
        try:
            rc, out, err = _ssh(PI_KEY, PI_USER, PI_HOST, cmd)
        except subprocess.TimeoutExpired:
            _record_error(state, f"pi/{name}", "ssh timed out")
            continue
        except Exception as exc:
            _record_error(state, f"pi/{name}", f"{type(exc).__name__}: {exc}")
            continue

        if rc != 0:
            _record_error(state, f"pi/{name}", (err or out).strip())
            continue

        try:
            data = json.loads(out.strip())
        except json.JSONDecodeError as exc:
            _record_error(state, f"pi/{name}", f"bad JSON: {exc}; out={out!r}")
            continue

        at = data.get("at")

        if name == "soil":
            val = data.get("pct_vwc")
            if val is not None:
                state["readings"]["soil_moisture"] = {
                    "value": val, "unit": "pct_vwc", "at": at, "stale": False,
                }
                _update_day_range(state, "soil_moisture", val, today)
        elif name == "bme280":
            tf = data.get("temp_f")
            rh = data.get("humidity_pct")
            if tf is not None:
                state["readings"]["temp_f"] = {
                    "value": tf, "unit": "fahrenheit", "at": at, "stale": False,
                }
                _update_day_range(state, "temp_f", tf, today)
            if rh is not None:
                state["readings"]["humidity_pct"] = {
                    "value": rh, "unit": "pct_rh", "at": at, "stale": False,
                }
                _update_day_range(state, "humidity_pct", rh, today)
        elif name == "lux":
            val = data.get("lux")
            if val is not None:
                state["readings"]["lux"] = {
                    "value": val, "unit": "lux", "at": at, "stale": False,
                }

    _save_state(state)
    print(json.dumps({"ok": True, "action": "sensors", "at": state["updated_at"]}))


def cmd_photo() -> None:
    state = _load_state()
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        rc, out, err = _ssh(
            JETSON_KEY, JETSON_USER, JETSON_HOST,
            f"{JETSON_VENV_PYTHON} -m clawfarmer_jetson capture "
            f"--out {JETSON_PHOTO_DIR}",
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        _record_error(state, "jetson/capture", "ssh timed out")
        _save_state(state)
        print(json.dumps({"ok": False, "action": "photo", "error": "timeout"}))
        sys.exit(1)
    except Exception as exc:
        _record_error(state, "jetson/capture", f"{type(exc).__name__}: {exc}")
        _save_state(state)
        print(json.dumps({"ok": False, "action": "photo",
                          "error": f"{type(exc).__name__}: {exc}"}))
        sys.exit(1)

    if rc != 0:
        _record_error(state, "jetson/capture", (err or out).strip())
        _save_state(state)
        print(json.dumps({"ok": False, "action": "photo",
                          "error": (err or out).strip()[:200]}))
        sys.exit(1)

    try:
        data = json.loads(out.strip())
    except json.JSONDecodeError as exc:
        _record_error(state, "jetson/capture", f"bad JSON: {exc}; out={out!r}")
        _save_state(state)
        print(json.dumps({"ok": False, "action": "photo", "error": "bad JSON"}))
        sys.exit(1)

    if not data.get("ok"):
        _record_error(state, "jetson/capture", data.get("error", "capture ok:false"))
        _save_state(state)
        print(json.dumps({"ok": False, "action": "photo",
                          "error": data.get("error")}))
        sys.exit(1)

    filename = data.get("filename")
    if not filename:
        _record_error(state, "jetson/capture", "capture JSON missing filename")
        _save_state(state)
        sys.exit(1)

    rc2, out2, err2 = _scp(
        JETSON_KEY, JETSON_USER, JETSON_HOST,
        f"{JETSON_PHOTO_DIR}/{filename}",
        PHOTOS_DIR / filename,
    )
    if rc2 != 0:
        _record_error(state, "jetson/scp", (err2 or out2).strip())
        _save_state(state)
        print(json.dumps({"ok": False, "action": "photo", "error": "scp failed"}))
        sys.exit(1)

    state["last_photo"] = {"filename": filename, "at": data.get("at")}
    _save_state(state)
    print(json.dumps({
        "ok": True, "action": "photo",
        "filename": filename, "size_bytes": data.get("size_bytes"),
    }))


def main() -> None:
    p = argparse.ArgumentParser(prog="clawfarmer-host-tick")
    p.add_argument("action", choices=("sensors", "photo"))
    args = p.parse_args()
    if args.action == "sensors":
        cmd_sensors()
    else:
        cmd_photo()


if __name__ == "__main__":
    main()
